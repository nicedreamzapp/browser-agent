"""recorder.py -- the Broker Recorder: browser jobs become training trajectories.

Off unless BROKER_RECORD=1, or a client asks for its own job to be recorded
(POST /lease {"record": true}, or the X-Broker-Record: 1 header on a CDP
websocket). When off, proxy.py holds REC = None and nothing here runs.

How it stays out of the way of live CDP traffic:
  * The proxy calls a handful of hot-path methods (command, reply, human, ...).
    Each does a set lookup and a queue.put_nowait(). Nothing is parsed, copied,
    awaited or written on the proxy's event loop. A full queue drops the event
    and counts it; it never waits.
  * Everything else runs in one daemon thread with its own asyncio loop: matching
    replies to commands, the page snapshot ("observation") taken over its OWN
    page socket to the browser, optional screenshots, scrubbing, and the writes.
  * The observation for a step is the newest snapshot of that tab, taken after
    the previous step settled. The action is never held back to take a fresh
    one; a step whose snapshot predates the previous action says so ("stale").

Privacy: every record is built from named fields only, through scrub.py, and
the finished record goes through scrub.deep_scrub() once more before it is
written. See docs/trajectory-schema.md.

Output: ~/.local/broker-recordings (BROKER_RECORD_DIR), directories 0700, files
0600, one JSONL per job: <day>/<job>.jsonl, screenshots in <day>/<job>/.
"""
import asyncio
import base64
import hashlib
import itertools
import json
import os
import queue
import re
import struct
import threading
import time
import zlib
from datetime import datetime

import scrub

SCHEMA = 1
DEFAULT_DIR = "~/.local/broker-recordings"
QUEUE_MAX = int(os.environ.get("BROKER_RECORD_QUEUE", 5000))
SETTLE = 0.6          # seconds after a step before the tab is snapshotted again
SETTLE_NAV = 1.5      # ... after something that probably loads a page
TYPE_GAP = 2.5        # keystrokes further apart than this start a new "type" step
HUMAN_GAP = 2.0       # the human's same-kind events closer than this are one step
REPLY_WAIT = 15       # a command with no reply by then is written without a result
MAX_ELEMENTS = 150
MAX_WALK = 2000       # DOM nodes a snapshot looks at, at most
TRUST_SECS = float(os.environ.get("BROKER_RECORD_TRUST_SECS", 5))   # typed text is kept only into a field
                      # clicked, or seen focused in a fresh snapshot, this recently
KEEP_DAYS = int(os.environ.get("BROKER_RECORD_KEEP_DAYS", 14))
MAX_BYTES = int(float(os.environ.get("BROKER_RECORD_MAX_MB", 2048)) * 1024 * 1024)

# ---------------------------------------------------------------------------
# which CDP commands are steps (everything else is never recorded at all)
# ---------------------------------------------------------------------------
ACTION_METHODS = frozenset({
    "Page.navigate", "Page.reload", "Page.navigateToHistoryEntry",
    "Input.dispatchMouseEvent", "Input.dispatchKeyEvent", "Input.insertText",
    "Input.dispatchTouchEvent", "Input.synthesizeTapGesture", "Input.synthesizeScrollGesture",
    "DOM.setFileInputFiles", "DOM.focus", "DOM.setAttributeValue", "DOM.setNodeValue",
    "Runtime.evaluate", "Runtime.callFunctionOn",
    "Target.createTarget", "Target.closeTarget",
})
_MOUSE_STEPS = ("mousePressed", "mouseWheel")
_KEY_STEPS = ("keyDown", "rawKeyDown", "char")


def recordable(m):
    """Hot path: is this client command a step? A dict lookup or two, no copies."""
    method = m.get("method")
    if method not in ACTION_METHODS:
        return False
    if method == "Input.dispatchMouseEvent":
        return (m.get("params") or {}).get("type") in _MOUSE_STEPS
    if method == "Input.dispatchKeyEvent":
        return (m.get("params") or {}).get("type") in _KEY_STEPS
    if method == "Input.dispatchTouchEvent":
        return (m.get("params") or {}).get("type") == "touchStart"
    return True


_REPLY_ID = re.compile(r'\s*\{\s*"id"\s*:\s*(\d+)')


def job_key(info, tid):
    """The job a broker tab belongs to: its REST lease, its browser client, or itself (a pool tab)."""
    if info is None:
        return "tab-" + tid[:12]
    if info.get("lease_id"):
        return info["lease_id"]
    if info.get("kind") == "ws":
        return info["owner"]
    return "pool-" + tid[:12]


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))[:80] or "job"


def _iso(t):
    return datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


# ---------------------------------------------------------------------------
# the page snapshot (runs in the page, read only)
# ---------------------------------------------------------------------------
# Never returns a field's value: only whether it is filled, and the attributes
# scrub.is_sensitive_field() decides on. `masks` are the boxes a screenshot must
# black out: every filled field, every password field, every iframe (a payment
# or captcha frame cannot be looked into).
SNAPSHOT_JS = r"""/*__bbSnap*/(() => {
  const MAX = %d, WALK = %d, PREV = __PREV_SIG__;
  const clean = (s, n) => (s || '').replace(/\s+/g, ' ').trim().slice(0, n || 80);
  const vw = innerWidth, vh = innerHeight;
  const ae = document.activeElement;
  // Cheap signature first: when nothing a step could change has changed, skip the walk.
  const all = document.getElementsByTagName('*').length;
  let filledN = 0, fi = 0;
  for (const f of document.querySelectorAll('input, textarea, select')) {
    if (++fi > WALK) break;
    if (f.value) filledN++;
  }
  const sig = [location.href, document.title, all, Math.round(scrollX), Math.round(scrollY), vw, vh,
               ae ? (ae.tagName + '#' + ae.id + '@' + (ae.getAttribute('name') || '')) : '', filledN].join('|');
  if (PREV && PREV === sig) return JSON.stringify({same: true, sig: sig});
  let walked = 0, truncated = false;
  const implicit = (el) => {
    const t = el.tagName.toLowerCase(), ty = (el.type || '').toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'button' || t === 'summary') return 'button';
    if (t === 'select') return el.multiple ? 'listbox' : 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'img') return 'img';
    if (/^h[1-6]$/.test(t)) return 'heading';
    if (t === 'label') return 'label';
    if (t === 'iframe') return 'iframe';
    if (t === 'input') {
      if (['submit', 'button', 'reset', 'image'].includes(ty)) return 'button';
      if (ty === 'checkbox') return 'checkbox';
      if (ty === 'radio') return 'radio';
      if (ty === 'range') return 'slider';
      if (ty === 'file') return 'button';
      if (ty === 'search') return 'searchbox';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return t;
  };
  const isField = (el) => /^(input|textarea|select)$/i.test(el.tagName) || el.isContentEditable;
  const labelOf = (el) => {
    let s = el.getAttribute('aria-label') || '';
    const lb = el.getAttribute('aria-labelledby');
    if (!s && lb) s = lb.split(/\s+/).map(i => { const n = document.getElementById(i); return n ? n.textContent : ''; }).join(' ');
    if (!s && el.labels && el.labels.length) s = el.labels[0].textContent;
    return clean(s, 80);
  };
  const nameOf = (el) => {
    const ty = (el.type || '').toLowerCase();
    if (isField(el)) {
      if (el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(ty)) return clean(el.value, 80);
      return labelOf(el) || clean(el.getAttribute('placeholder') || el.title || el.getAttribute('name') || '', 80);
    }
    return labelOf(el) || clean(el.getAttribute('alt') || el.title || el.textContent, 80);
  };
  const vis = (el, r) => {
    if (r.width < 1 || r.height < 1 || r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) return false;
    const st = getComputedStyle(el);
    return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
  };
  const box = (r) => [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  const masks = [];
  for (const el of document.querySelectorAll('input, textarea, select, [contenteditable=""], [contenteditable="true"], iframe')) {
    if (++walked > WALK) { truncated = true; break; }
    const ty = (el.type || '').toLowerCase();
    if (ty === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (!vis(el, r)) continue;
    const filled = el.isContentEditable ? !!clean(el.textContent) : (ty === 'checkbox' || ty === 'radio') ? false : !!el.value;
    if (el.tagName === 'IFRAME' || ty === 'password' || filled) masks.push(box(r));
    if (masks.length >= 500) break;
  }
  const sel = 'a[href], button, input, select, textarea, summary, iframe, [role], [contenteditable=""], [contenteditable="true"], [tabindex]:not([tabindex="-1"]), h1, h2, h3, img[alt]';
  const els = [];
  let focused = null;
  walked = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (++walked > WALK) break;
    const ty = (el.type || '').toLowerCase();
    if (ty === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (!vis(el, r)) continue;
    const e = {id: 'e' + els.length, tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || implicit(el),
               name: nameOf(el), box: box(r)};
    if (isField(el)) {
      e.field = true;
      e.type = el.isContentEditable ? 'contenteditable' : (ty || el.tagName.toLowerCase());
      e.ac = clean(el.getAttribute('autocomplete'), 40);
      e.fname = clean(el.getAttribute('name'), 40);
      e.fid = clean(el.id, 40);
      e.placeholder = clean(el.getAttribute('placeholder'), 60);
      e.label = labelOf(el);
      if (ty === 'checkbox' || ty === 'radio') e.checked = !!el.checked;
      else e.filled = el.isContentEditable ? !!clean(el.textContent) : !!el.value;
    }
    if (el === ae) focused = e.id;
    els.push(e);
    if (els.length >= MAX) break;
  }
  if (focused === null && ae && ae !== document.body && isField(ae)) {
    const r = ae.getBoundingClientRect();
    const ty = (ae.type || '').toLowerCase();
    els.push({id: 'e' + els.length, tag: ae.tagName.toLowerCase(), role: ae.getAttribute('role') || implicit(ae),
              name: nameOf(ae), box: box(r), field: true, type: ty || ae.tagName.toLowerCase(),
              ac: clean(ae.getAttribute('autocomplete'), 40), fname: clean(ae.getAttribute('name'), 40),
              fid: clean(ae.id, 40), placeholder: clean(ae.getAttribute('placeholder'), 60), label: labelOf(ae),
              filled: !!ae.value, offscreen: true});
    focused = els[els.length - 1].id;
  }
  return JSON.stringify({url: location.href, title: document.title, vw: vw, vh: vh, dpr: devicePixelRatio,
                         sx: Math.round(scrollX), sy: Math.round(scrollY), focused: focused, els: els, masks: masks,
                         masks_truncated: truncated, sig: sig});
})()""" % (MAX_ELEMENTS, MAX_WALK)


def snapshot_js(prev_sig=None):
    """The snapshot expression; with the last snapshot's signature it answers {"same": true} cheaply."""
    return SNAPSHOT_JS.replace("__PREV_SIG__", json.dumps(prev_sig) if prev_sig else "null")


def element_view(e, private=False):
    """One snapshot element as written: never a value, never raw attribute names.
    On a private host, no name either: role, tag and box only."""
    if not e:
        return None
    out = {"node": e.get("id"), "role": str(e.get("role") or e.get("tag") or "")[:30],
           "tag": str(e.get("tag") or "")[:20], "box": e.get("box")}
    if not private:
        out["name"] = scrub.scrub_text(e.get("name") or "", 80)
    if e.get("field"):
        out["input_type"] = str(e.get("type") or "")[:20]
        out["sensitive"] = scrub.is_sensitive_field(e)
        if "checked" in e:
            out["checked"] = bool(e["checked"])
        else:
            out["filled"] = bool(e.get("filled"))
    return out


def observation_view(raw, private=False):
    """The compact snapshot written with a step. On a private host: the host, the
    elements' roles and boxes, and nothing else (no path, no title, no names)."""
    if not raw:
        return None
    return {"url": host_only(raw.get("url")) if private else scrub.scrub_url(raw.get("url")),
            "title": None if private else scrub.scrub_text(raw.get("title") or "", 200),
            "viewport": [raw.get("vw"), raw.get("vh")], "scroll": [raw.get("sx"), raw.get("sy")],
            "focused": raw.get("focused"),
            "elements": [element_view(e, private) for e in (raw.get("els") or [])[:MAX_ELEMENTS]],
            **({"private_host": True} if private else {})}


def hit_test(raw, x, y):
    """The smallest snapshot element whose box holds viewport point (x, y)."""
    best, area = None, None
    for e in (raw or {}).get("els") or []:
        b = e.get("box") or [0, 0, 0, 0]
        if b[0] <= x <= b[0] + b[2] and b[1] <= y <= b[1] + b[3]:
            a = b[2] * b[3]
            if area is None or a < area:
                best, area = e, a
    return best


def focused_element(raw):
    f = (raw or {}).get("focused")
    for e in (raw or {}).get("els") or []:
        if e.get("id") == f:
            return e
    return None


# ---------------------------------------------------------------------------
# screenshots: black out boxes in a PNG, standard library only
# ---------------------------------------------------------------------------
def _chunk(typ, body):
    return struct.pack(">I", len(body)) + typ + body + struct.pack(">I", zlib.crc32(typ + body) & 0xffffffff)


def png_blackout(data, boxes, scale=1.0):
    """`data` with every box (CSS px [x, y, w, h], times `scale` for device px) painted
    black. None when the PNG is not one this can safely rewrite: the caller then
    drops the screenshot instead of writing an unmasked one."""
    try:
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        pos, ihdr, idat = 8, None, []
        while pos + 8 <= len(data):
            ln = struct.unpack(">I", data[pos:pos + 4])[0]
            typ = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + ln]
            pos += 12 + ln
            if typ == b"IHDR":
                ihdr = body
            elif typ == b"IDAT":
                idat.append(body)
            elif typ == b"IEND":
                break
        if ihdr is None or not idat:
            return None
        w, h, depth, ctype, _comp, _flt, inter = struct.unpack(">IIBBBBB", ihdr)
        if depth != 8 or inter != 0 or ctype not in (2, 6):
            return None
        if not boxes:
            return data
        bpp = 3 if ctype == 2 else 4
        stride = w * bpp
        raw = zlib.decompress(b"".join(idat))
        if len(raw) < h * (stride + 1):
            return None
        rows, prev, i = [], bytearray(stride), 0
        for _ in range(h):
            f = raw[i]
            line = bytearray(raw[i + 1:i + 1 + stride])
            i += 1 + stride
            if f == 1:
                for x in range(bpp, stride):
                    line[x] = (line[x] + line[x - bpp]) & 0xff
            elif f == 2:
                line = bytearray((a + b) & 0xff for a, b in zip(line, prev))
            elif f == 3:
                for x in range(stride):
                    left = line[x - bpp] if x >= bpp else 0
                    line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xff
            elif f == 4:
                for x in range(stride):
                    a = line[x - bpp] if x >= bpp else 0
                    b = prev[x]
                    c = prev[x - bpp] if x >= bpp else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    line[x] = (line[x] + pr) & 0xff
            elif f != 0:
                return None
            rows.append(line)
            prev = line
        black = b"\x00\x00\x00" if bpp == 3 else b"\x00\x00\x00\xff"
        for bx in boxes:
            x0 = max(0, int((bx[0] - 2) * scale))
            y0 = max(0, int((bx[1] - 2) * scale))
            x1 = min(w, int((bx[0] + bx[2] + 2) * scale) + 1)
            y1 = min(h, int((bx[1] + bx[3] + 2) * scale) + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            fill = black * (x1 - x0)
            for y in range(y0, y1):
                rows[y][x0 * bpp:x1 * bpp] = fill
        out = b"".join(b"\x00" + bytes(r) for r in rows)
        return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(out, 6))
                + _chunk(b"IEND", b""))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# turning one CDP command into a semantic action (raw: kept in memory only)
# ---------------------------------------------------------------------------
_KEY_NAMES = {"\r": "Enter", "\n": "Enter", "\t": "Tab", "\x08": "Backspace", "\x1b": "Escape"}


def _mods(n):
    n = int(n or 0)
    return [name for bit, name in ((2, "Ctrl"), (4, "Meta"), (1, "Alt"), (8, "Shift")) if n & bit]


def semantic(method, p):
    """(action dict with raw text, needs_target kind). Raw text never leaves the worker unscrubbed."""
    p = p or {}
    if method == "Page.navigate":
        return {"type": "navigate", "url": p.get("url")}, None
    if method == "Page.reload":
        return {"type": "reload"}, None
    if method == "Page.navigateToHistoryEntry":
        return {"type": "history", "entry": p.get("entryId")}, None
    if method == "Input.dispatchMouseEvent":
        x, y = p.get("x", 0), p.get("y", 0)
        if p.get("type") == "mouseWheel":
            return {"type": "scroll", "x": x, "y": y, "dx": p.get("deltaX", 0), "dy": p.get("deltaY", 0)}, "point"
        return {"type": "click", "x": x, "y": y, "button": p.get("button", "left"),
                "count": p.get("clickCount", 1)}, "point"
    if method == "Input.synthesizeTapGesture":
        return {"type": "click", "x": p.get("x", 0), "y": p.get("y", 0), "button": "left", "count": 1}, "point"
    if method == "Input.dispatchTouchEvent":
        pts = p.get("touchPoints") or [{}]
        return {"type": "click", "x": pts[0].get("x", 0), "y": pts[0].get("y", 0), "button": "touch", "count": 1}, "point"
    if method == "Input.synthesizeScrollGesture":
        return {"type": "scroll", "x": p.get("x", 0), "y": p.get("y", 0),
                "dx": -(p.get("xDistance") or 0), "dy": -(p.get("yDistance") or 0)}, "point"
    if method == "Input.insertText":
        return {"type": "type", "text": p.get("text") or "", "via": "insertText"}, "focus"
    if method == "Input.dispatchKeyEvent":
        t, text, key = p.get("type"), p.get("text"), p.get("key") or ""
        mods = [m for m in _mods(p.get("modifiers")) if m != "Shift"]
        if text in _KEY_NAMES or key in ("Enter", "Tab", "Backspace", "Escape", "Delete") or \
                (key and len(key) > 1 and not text):
            if t == "char":
                return None, None             # the keyDown before it already said so
            return {"type": "key", "key": _KEY_NAMES.get(text) or key or p.get("code") or "?",
                    "modifiers": mods}, "focus"
        if mods:
            if t == "char":
                return None, None
            return {"type": "key", "key": (key or text or "?")[:20], "modifiers": mods}, "focus"
        if t == "rawKeyDown":
            return None, None                 # the char event that follows carries the text
        if text:
            return {"type": "type", "text": text, "via": "keys"}, "focus"
        return None, None
    if method == "DOM.setFileInputFiles":
        files = [os.path.basename(str(f)) for f in (p.get("files") or [])]
        return {"type": "upload", "files": files}, "focus"
    if method == "DOM.focus":
        return {"type": "focus"}, None
    if method in ("DOM.setAttributeValue", "DOM.setNodeValue"):
        return {"type": "set_value", "attribute": p.get("name") or "nodeValue",
                "text": p.get("value") or ""}, "focus"
    if method in ("Runtime.evaluate", "Runtime.callFunctionOn"):
        code = p.get("expression") if method == "Runtime.evaluate" else p.get("functionDeclaration")
        code = code or ""
        kind = "js"
        if re.search(r"selectedIndex|\.selected\s*=|selectOption|options\[", code):
            kind = "select"
        return {"type": kind, "code": code, "args": len(p.get("arguments") or [])}, "focus"
    if method == "Target.createTarget":
        return {"type": "open_tab", "url": p.get("url") or "about:blank"}, None
    if method == "Target.closeTarget":
        return {"type": "close_tab"}, None
    return None, None


def host_only(url):
    """scheme://host/ and nothing else, for a private host."""
    try:
        from urllib.parse import urlsplit
        p = urlsplit(url or "")
        return f"{p.scheme}://{p.hostname}/" if p.hostname else None
    except ValueError:
        return None


def safe_action(a, target, trusted=True, private=False):
    """The written form of an action. Built field by field; nothing raw is copied.

    `trusted`: the target is known to be where the text went (clicked or seen focused
    in a fresh snapshot moments before). `private`: the page is on a denylisted host,
    so only the action itself is written, with no text of any kind."""
    t = a["type"]
    out = {"type": t, "method": a.get("method")}
    redacted = False
    if private:
        if t in ("click", "scroll"):
            for k in ("x", "y", "dx", "dy", "count"):
                if isinstance(a.get(k), (int, float)):
                    out[k] = round(a[k], 1)
        elif t == "key":
            out["key"] = str(a.get("key"))[:20]
            out["modifiers"] = a.get("modifiers") or []
        elif t in ("navigate", "open_tab"):
            out["url"] = host_only(a.get("url"))
        elif t == "upload":
            out["count"] = len(a.get("files") or [])
        if t in ("type", "set_value", "js", "select", "upload"):
            out["redacted"] = True
        out["private_host"] = True
        return {k: v for k, v in out.items() if v is not None}
    if t in ("navigate", "open_tab"):
        out["url"] = scrub.scrub_url(a.get("url"))
    elif t == "history":
        out["entry"] = a.get("entry") if isinstance(a.get("entry"), int) else None
    elif t in ("click", "scroll"):
        for k in ("x", "y", "dx", "dy", "count"):
            if k in a:
                v = a[k]
                out[k] = round(v, 1) if isinstance(v, (int, float)) else 0
        if "button" in a:
            out["button"] = str(a["button"])[:10]
    elif t == "key":
        out["key"] = str(a.get("key"))[:20]
        out["modifiers"] = a.get("modifiers") or []
    elif t in ("type", "set_value"):
        text, changed = scrub.scrub_typed(a.get("text") or "", target, trusted)
        out["text"] = text
        redacted = text == scrub.REDACTED          # the whole thing withheld
        if changed and not redacted:
            out["masked"] = True                   # emails, phones, tokens replaced by placeholders
        out["via"] = a.get("via")
        if t == "set_value":
            out["attribute"] = scrub.scrub_text(str(a.get("attribute"))[:40])
    elif t == "upload":
        names = a.get("files") or []
        out["files"] = [scrub.scrub_text(n, 80) for n in names[:10]]
        out["count"] = len(names)
    elif t in ("js", "select"):
        sens = target is not None and scrub.is_sensitive_field(target)
        code = a.get("code") or ""
        out["code"] = scrub.scrub_js(code, 2000, force_literals=True, sensitive=sens)
        out["args"] = a.get("args", 0)
        redacted = out["code"] != code[:2000]
    if redacted:
        out["redacted"] = True
    return {k: v for k, v in out.items() if v is not None}


# ---------------------------------------------------------------------------
# the hot-path facade the proxy calls
# ---------------------------------------------------------------------------
class Recorder:
    def __init__(self, upstream_port, out_dir=None, all_jobs=True, screenshots=None,
                 queue_max=QUEUE_MAX, log=None, start=True, screenshot_hosts=None,
                 keep_days=KEEP_DAYS, max_bytes=MAX_BYTES):
        self.upstream_port = upstream_port            # callable: proxy.UPSTREAM may change
        self.dir = os.path.expanduser(out_dir or os.environ.get("BROKER_RECORD_DIR") or DEFAULT_DIR)
        self.all_jobs = all_jobs
        self.screenshots = (os.environ.get("BROKER_RECORD_SCREENSHOTS") == "1") if screenshots is None else screenshots
        # Screenshots only on hosts named here (comma-separated); empty means none at all.
        self.screenshot_hosts = [h for h in (screenshot_hosts if screenshot_hosts is not None else
                                 os.environ.get("BROKER_RECORD_SCREENSHOT_HOSTS", "").split(",")) if h.strip()]
        self.deny_hosts = [h for h in os.environ.get("BROKER_RECORD_DENY_HOSTS", "").split(",") if h.strip()]
        self.keep_days, self.max_bytes = keep_days, max_bytes
        self.flagged = set()
        self.q = queue.Queue(queue_max)
        self.dropped = 0
        self.pending = {}          # conn -> {command ids awaiting a reply}; broker loop only
        self._seq = itertools.count(1)
        self.log = log or (lambda msg: None)
        self.worker = _Worker(self)
        self._thread = None
        if start:
            self.start()

    @classmethod
    def from_env(cls, upstream_port, log=None):
        if os.environ.get("BROKER_RECORD") == "1":
            return cls(upstream_port, all_jobs=True, log=log)
        return None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self.worker.run, name="broker-recorder", daemon=True)
            self._thread.start()

    def stop(self, timeout=5):
        """Finish every open job and stop the thread (tests, shutdown)."""
        if self._thread is None:
            return
        try:
            self.q.put(("stop",), timeout=timeout)
        except queue.Full:
            return
        self._thread.join(timeout)

    def flush(self, timeout=5):
        """Block until everything queued so far is written (tests)."""
        ev = threading.Event()
        try:
            self.q.put(("flush", ev), timeout=timeout)
        except queue.Full:
            return False
        return ev.wait(timeout)

    # ---- hot path: O(1), never blocks -------------------------------------------
    def _put(self, ev):
        try:
            self.q.put_nowait(ev)
        except queue.Full:
            self.dropped += 1

    def wants(self, job):
        return self.all_jobs or job in self.flagged

    def flag(self, job):
        self.flagged.add(job)

    def job(self, job, owner=None, task=None, kind=None, tab=None):
        self._put(("job", time.time(), job, owner, task, kind, tab))

    def command(self, conn, job, tid, m, refused=None, result=None):
        if not recordable(m):
            return
        mid = m.get("id")
        if refused is None and result is None and mid is not None:
            self.pending.setdefault(conn, set()).add(mid)
        self._put(("cmd", time.time(), next(self._seq), conn, job, tid, m, refused, result))

    def reply(self, conn, raw):
        """An upstream message on a recorded page socket. Only replies to steps go on."""
        ids = self.pending.get(conn)
        if not ids:
            return
        head = raw[:40]
        if isinstance(head, bytes):
            head = head.decode("latin-1")
        mm = _REPLY_ID.match(head)
        if mm and int(mm.group(1)) in ids:
            ids.discard(int(mm.group(1)))
            self._put(("reply", time.time(), conn, raw))

    def reply_msg(self, conn, m):
        """Same, for a message the proxy has already parsed (browser sessions)."""
        ids = self.pending.get(conn)
        if ids and m.get("id") in ids and "method" not in m:
            ids.discard(m["id"])
            self._put(("reply", time.time(), conn, m))

    def reply_hook(self, conn):
        async def hook(raw):
            self.reply(conn, raw)
            return raw
        return hook

    def conn_closed(self, conn):
        self.pending.pop(conn, None)

    def human(self, tid, t, kind):
        self._put(("human", time.time(), tid, t, kind))

    def tab_gone(self, tid, why=""):
        self._put(("tabgone", time.time(), tid, why))

    def outcome(self, job, outcome=None, task=None, note=None):
        self._put(("outcome", time.time(), job, outcome, task, note))

    def end(self, job, why=""):
        self._put(("end", time.time(), job, why))


# ---------------------------------------------------------------------------
# the worker thread
# ---------------------------------------------------------------------------
class _Job:
    def __init__(self, key, owner=None, task=None, kind=None, started=None):
        self.key, self.owner, self.task, self.kind = key, owner, task, kind
        self.started = started or time.time()
        self.tabs = set()
        self.path = None
        self.steps = 0
        self.redacted = 0
        self.outcome = None
        self.note = None
        self.typing = None         # an open "type" step being coalesced
        self.human = None          # an open human step being coalesced
        self.last_obs_hash = None
        self.last_obs_seq = None
        self.ended = False


class _PageConn:
    """The recorder's own page socket to one tab (snapshots and screenshots only)."""

    def __init__(self, url):
        self.url, self.ws, self._id, self._pending, self._reader = url, None, 0, {}, None

    async def call(self, method, params, timeout=5):
        from websockets.asyncio.client import connect as ws_connect
        if self.ws is None:
            self.ws = await asyncio.wait_for(ws_connect(self.url, max_size=None, ping_interval=None), 3)
            self._reader = asyncio.ensure_future(self._read())
        self._id += 1
        mid = self._id
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            m = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(mid, None)
        if "error" in m:
            raise RuntimeError((m["error"] or {}).get("message"))
        return m.get("result") or {}

    async def _read(self):
        try:
            async for raw in self.ws:
                if not raw.startswith('{"id"') and '"id"' not in raw[:40]:
                    continue
                m = json.loads(raw)
                fut = self._pending.pop(m.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(m)
        except Exception:
            pass
        self.ws = None
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(ConnectionError("page socket closed"))

    async def close(self):
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self._reader is not None:
            self._reader.cancel()


class _Worker:
    def __init__(self, rec):
        self.rec = rec
        self.jobs = {}             # job key -> _Job
        self.tab_job = {}          # target id -> job key
        self.obs = {}              # target id -> {"raw", "t", "shot": bytes|None, "scale"}
        self.last_step = {}        # target id -> time its last step finished
        self.last_click = {}       # target id -> (t, element)
        self.cmds = {}             # (conn, id) -> draft step
        self.conns = {}            # target id -> _PageConn
        self.refresh = {}          # target id -> pending refresh task
        self.loop = None
        self.errors = 0
        self.last_sweep = 0

    # ---- plumbing ---------------------------------------------------------------
    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._main())
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    async def _main(self):
        q = self.rec.q
        last_tick = time.time()
        while True:
            try:
                ev = await self.loop.run_in_executor(None, _get, q, 0.5)
            except Exception:
                ev = None
            if ev is not None:
                if ev[0] == "stop":
                    await self._stop()
                    return
                if ev[0] == "flush":
                    await asyncio.sleep(0)
                    self._flush_all(time.time(), force=False)
                    ev[1].set()
                    continue
                try:
                    await self._handle(ev)
                except Exception as e:
                    self.errors += 1
                    self.rec.log(f"recorder: {ev[0]} not recorded ({type(e).__name__}: {e})")
            now = time.time()
            if now - last_tick >= 0.5 or ev is None:
                last_tick = now
                self._tick(now)

    async def _stop(self):
        for task in list(self.refresh.values()):
            task.cancel()
        await asyncio.gather(*self.refresh.values(), return_exceptions=True)
        self.refresh.clear()
        for key in list(self.jobs):
            self._end(key, "broker stopped", time.time())
        for c in list(self.conns.values()):
            await c.close()
        self.conns.clear()

    def _job(self, key, t=None):
        j = self.jobs.get(key)
        if j is None or j.ended:
            j = self.jobs[key] = _Job(key, started=t)
        return j

    def _attach(self, j, tid):
        if not tid:
            return
        if tid not in j.tabs:
            j.tabs.add(tid)
            self.tab_job[tid] = j.key
            if tid not in self.obs:
                self._schedule(tid, 0.05)

    # ---- events -----------------------------------------------------------------
    async def _handle(self, ev):
        kind = ev[0]
        if kind == "job":
            _, t, key, owner, task, jkind, tab = ev
            j = self._job(key, t)
            j.owner = owner or j.owner
            j.task = task or j.task
            j.kind = jkind or j.kind
            self._attach(j, tab)
        elif kind == "cmd":
            self._command(*ev[1:])
        elif kind == "reply":
            _, t, conn, raw = ev
            m = raw if isinstance(raw, dict) else json.loads(raw)
            d = self.cmds.pop((conn, m.get("id")), None)
            if d is not None:
                self._finish(d, m.get("error"), t)
        elif kind == "human":
            self._human(*ev[1:])
        elif kind == "tabgone":
            _, t, tid, why = ev
            key = self.tab_job.pop(tid, None)
            self.obs.pop(tid, None)
            self.last_click.pop(tid, None)
            c = self.conns.pop(tid, None)
            if c is not None:
                await c.close()
            j = self.jobs.get(key) if key else None
            if j is not None:
                j.tabs.discard(tid)
                if not j.tabs and j.kind in ("rest", "pool", None):
                    self._end(key, why or "tab closed", t)
        elif kind == "outcome":
            _, t, key, outcome, task, note = ev
            j = self._job(key, t)
            if outcome:
                j.outcome = str(outcome)[:200]
            if task:
                j.task = task
            if note:
                j.note = str(note)[:500]
        elif kind == "end":
            _, t, key, why = ev
            if key in self.jobs:
                self._end(key, why, t)

    def _command(self, t, seq, conn, key, tid, m, refused, result):
        j = self._job(key, t)
        if j.kind is None:
            j.kind = "ws" if str(key).startswith("ws:") else ("pool" if str(key).startswith("pool-") else "rest")
        method = m.get("method")
        a, need = semantic(method, m.get("params"))
        if a is None:
            # Its id stays in rec.pending until the reply passes; only the proxy's event
            # loop ever changes rec.pending, so there is nothing to race with.
            return
        a["method"] = method
        if method == "Target.createTarget" and result:
            tid = result.get("targetId") or tid
        self._attach(j, tid)
        o = self.obs.get(tid) if tid else None
        raw = o["raw"] if o else None
        target = None
        stale = bool(o and self.last_step.get(tid, 0) > o["t"])
        trusted = False
        if need == "point" and raw is not None:
            target = hit_test(raw, a.get("x", 0), a.get("y", 0))
        elif need == "focus":
            lc = self.last_click.get(tid)
            if lc and t - lc[0] < TRUST_SECS and lc[1] and lc[1].get("field"):
                target, trusted = lc[1], True      # the field it clicked moments ago
            elif raw is not None:
                target = focused_element(raw)
                # focused in a snapshot taken after the last step, moments ago
                trusted = target is not None and not stale and t - o["t"] < TRUST_SECS
        if a["type"] == "click" and tid:
            self.last_click[tid] = (t, target)
        d = {"t": t, "seq": seq, "job": key, "tid": tid, "action": a, "target": target,
             "obs": o, "stale": stale, "trusted": trusted, "conn": conn}
        if refused is not None:
            self._finish(d, {"message": refused, "by": "broker"}, t)
        elif result is not None or m.get("id") is None:
            self._finish(d, None, t)
        else:
            self.cmds[(conn, m.get("id"))] = d

    def _finish(self, d, error, t):
        d["error"] = error
        d["done"] = t
        tid = d["tid"]
        a = d["action"]
        if tid:
            self.last_step[tid] = t
            nav = a["type"] in ("navigate", "reload", "history", "open_tab") or \
                (a["type"] == "key" and a.get("key") == "Enter") or a["type"] == "click"
            self._schedule(tid, SETTLE_NAV if nav else SETTLE)
        j = self._job(d["job"], d["t"])
        if a["type"] == "type" and not error:
            ty = j.typing
            same = ty is not None and ty["tid"] == tid and t - ty["done"] < TYPE_GAP and \
                (ty["target"] or {}).get("id") == (d["target"] or {}).get("id")
            if same:
                ty["action"]["text"] += a.get("text", "")
                ty["done"] = t
                return
            self._flush_typing(j)
            j.typing = d
            return
        self._flush_typing(j)
        self._flush_human(j)
        self._write_step(j, d, "agent")

    def _human(self, t, tid, ht, kind):
        key = self.tab_job.get(tid)
        if key is None:
            return
        j = self._job(key, t)
        typ = {"pointerdown": "click", "keydown": "key", "input": "type", "change": "type",
               "paste": "paste", "wheel": "scroll", "frame": "click"}.get(str(kind), "other")
        h = j.human
        if h is not None and h["tid"] == tid and h["action"]["type"] == typ and t - h["done"] < HUMAN_GAP:
            h["action"]["count"] += 1
            h["done"] = t
            return
        self._flush_typing(j)
        self._flush_human(j)
        o = self.obs.get(tid)
        raw = o["raw"] if o else None
        target = focused_element(raw) if typ in ("key", "type", "paste") else None
        j.human = {"t": t, "seq": next(self.rec._seq), "job": key, "tid": tid,
                   "action": {"type": typ, "count": 1, "event": str(kind)[:20]},
                   "target": target, "obs": o,
                   "stale": bool(o and self.last_step.get(tid, 0) > o["t"]), "done": t, "error": None}
        self.last_step[tid] = t
        self._schedule(tid, SETTLE_NAV)

    def _flush_typing(self, j):
        if j.typing is not None:
            d, j.typing = j.typing, None
            self._write_step(j, d, "agent")

    def _flush_human(self, j):
        if j.human is not None:
            d, j.human = j.human, None
            self._write_step(j, d, "human")

    def _flush_all(self, now, force=True):
        for j in list(self.jobs.values()):
            if j.typing is not None and (force or now - j.typing["done"] >= 0):
                self._flush_typing(j)
            if j.human is not None:
                self._flush_human(j)

    def _tick(self, now):
        for j in list(self.jobs.values()):
            if j.typing is not None and now - j.typing["done"] >= TYPE_GAP:
                self._flush_typing(j)
            if j.human is not None and now - j.human["done"] >= HUMAN_GAP:
                self._flush_human(j)
        for k, d in list(self.cmds.items()):
            if now - d["t"] > REPLY_WAIT:
                self.cmds.pop(k, None)
                d["no_reply"] = True
                self._finish(d, None, now)
        if now - self.last_sweep > 86400:
            self.last_sweep = now
            try:
                gone = sweep(self.rec.dir, self.rec.keep_days, self.rec.max_bytes, now)
                if gone:
                    self.rec.log(f"recorder: removed {len(gone)} old recording day(s): {', '.join(gone)}")
            except Exception as e:
                self.rec.log(f"recorder: sweep failed ({type(e).__name__}: {e})")

    # ---- observations -------------------------------------------------------------
    def _schedule(self, tid, delay):
        old = self.refresh.get(tid)
        if old is not None and not old.done():
            old.cancel()
        self.refresh[tid] = asyncio.ensure_future(self._refresh_later(tid, delay))

    async def _refresh_later(self, tid, delay):
        try:
            await asyncio.sleep(delay)
            await self._snapshot(tid)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _snapshot(self, tid):
        if tid not in self.tab_job:
            return
        c = self.conns.get(tid)
        if c is None:
            c = self.conns[tid] = _PageConn(f"ws://127.0.0.1:{self.rec.upstream_port()}/devtools/page/{tid}")
        t = time.time()
        prev = self.obs.get(tid)
        prev_sig = (prev or {}).get("raw", {}).get("sig") if prev else None
        try:
            res = await c.call("Runtime.evaluate", {"expression": snapshot_js(prev_sig), "returnByValue": True}, 4)
            raw = json.loads((res.get("result") or {}).get("value") or "null")
        except Exception:
            self.conns.pop(tid, None)
            await c.close()
            return
        if isinstance(raw, dict) and raw.get("same") and prev is not None:
            # Nothing a step could change has changed: the last snapshot stands, as of now.
            self.obs[tid] = dict(prev, t=t)
            return
        if not isinstance(raw, dict) or not isinstance(raw.get("els"), list):
            return
        o = {"raw": raw, "t": t, "shot": None}
        if self.rec.screenshots and self._shot_allowed(raw):
            o["shot"] = await self._screenshot(c, raw)
        self.obs[tid] = o

    def _shot_allowed(self, raw):
        """Screenshots only on an explicitly allowed host, never a private one, never
        when the mask walk was cut short (a field could be left unmasked)."""
        url = raw.get("url") or ""
        return (scrub.host_allowed(url, self.rec.screenshot_hosts)
                and not scrub.host_denied(url, self.rec.deny_hosts)
                and not raw.get("masks_truncated"))

    async def _screenshot(self, c, raw):
        """A PNG of the viewport with every filled field, password field and iframe
        blacked out, or None. The masks are read again after the capture; if they
        moved (the page scrolled or changed), the screenshot is thrown away."""
        try:
            scale = 0.5
            res = await c.call("Page.captureScreenshot", {
                "format": "png", "optimizeForSpeed": True, "captureBeyondViewport": False,
                "clip": {"x": raw.get("sx", 0), "y": raw.get("sy", 0), "width": raw.get("vw", 0),
                         "height": raw.get("vh", 0), "scale": scale}}, 8)
            data = base64.b64decode(res.get("data") or "")
            again = await c.call("Runtime.evaluate", {"expression": snapshot_js(), "returnByValue": True}, 4)
            raw2 = json.loads((again.get("result") or {}).get("value") or "null")
            if not isinstance(raw2, dict) or raw2.get("masks") != raw.get("masks") \
                    or (raw2.get("sx"), raw2.get("sy")) != (raw.get("sx"), raw.get("sy")):
                return None
            return await asyncio.get_running_loop().run_in_executor(
                None, png_blackout, data, raw.get("masks") or [], scale)
        except Exception:
            return None

    # ---- writing ------------------------------------------------------------------
    def _open(self, j):
        if j.path is not None:
            return
        day = datetime.fromtimestamp(j.started).strftime("%Y-%m-%d")
        d = os.path.join(self.rec.dir, day)
        _mkdir_private(self.rec.dir)
        _mkdir_private(d)
        j.path = os.path.join(d, _safe_name(j.key) + ".jsonl")
        n = 1
        while os.path.exists(j.path):
            n += 1
            j.path = os.path.join(d, f"{_safe_name(j.key)}-{n}.jsonl")
        self._append(j, {"type": "job", "schema": SCHEMA, "job": _safe_name(j.key),
                         "owner": scrub.scrub_text(j.owner or "", 80), "owner_kind": j.kind or "rest",
                         "task": scrub.scrub_text(j.task, 2000) if j.task else None,
                         "started": _iso(j.started), "t": round(j.started, 3),
                         "recorder": {"screenshots": bool(self.rec.screenshots)}})

    def _append(self, j, rec):
        line = json.dumps(scrub.deep_scrub(rec), ensure_ascii=False, separators=(",", ":"))
        fd = os.open(j.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)

    def _write_step(self, j, d, actor):
        self._open(j)
        a = d["action"]
        target = d.get("target")
        o = d.get("obs")
        raw = o["raw"] if o else None
        deny = self.rec.deny_hosts
        private = bool((raw and scrub.host_denied(raw.get("url"), deny))
                       or (a.get("type") in ("navigate", "open_tab") and scrub.host_denied(a.get("url"), deny)))
        if actor == "human":
            action = {"type": a["type"], "count": a.get("count", 1), "event": a.get("event")}
            if private:
                action["private_host"] = True
        else:
            action = safe_action(a, target, d.get("trusted", False), private)
        if action.get("redacted"):
            j.redacted += 1
        obs = observation_view(raw, private)
        rec = {"type": "step", "schema": SCHEMA, "job": _safe_name(j.key), "seq": d["seq"],
               "ts": _iso(d["t"]), "t": round(d["t"], 3), "tab": (d["tid"] or "")[:12], "actor": actor,
               "url": obs["url"] if obs else None, "title": obs["title"] if obs else None,
               "action": action, "target": element_view(target, private)}
        if obs is not None:
            h = hashlib.sha1(json.dumps(obs, sort_keys=True).encode()).hexdigest()
            if h == j.last_obs_hash and j.last_obs_seq is not None:
                rec["observation"] = {"same_as": j.last_obs_seq}
            else:
                rec["observation"] = obs
                j.last_obs_hash, j.last_obs_seq = h, d["seq"]
            rec["observation_age"] = round(max(0.0, d["t"] - o["t"]), 2)
            rec["observation_stale"] = bool(d.get("stale"))
        else:
            rec["observation"] = None
        shot = o.get("shot") if o and not private else None
        if shot:
            rel = os.path.join(os.path.basename(os.path.dirname(j.path)),
                               os.path.splitext(os.path.basename(j.path))[0], f"{d['seq']}.png")
            full = os.path.join(self.rec.dir, rel)
            _mkdir_private(os.path.dirname(full))
            fd = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, shot)
            finally:
                os.close(fd)
            rec["screenshot"] = rel
        else:
            rec["screenshot"] = None
        err = d.get("error")
        if d.get("no_reply"):
            rec["result"] = {"ok": None, "note": "no reply"}
        elif err:
            rec["result"] = {"ok": False, "error": scrub.scrub_text(str((err or {}).get("message") or err), 300)}
            if (err or {}).get("by") == "broker":
                rec["result"]["refused_by_broker"] = True
        else:
            rec["result"] = {"ok": True}
        self._append(j, rec)
        j.steps += 1

    def _end(self, key, why, t):
        j = self.jobs.get(key)
        if j is None or j.ended:
            return
        self._flush_typing(j)
        self._flush_human(j)
        for k, d in list(self.cmds.items()):
            if d["job"] == key:
                self.cmds.pop(k, None)
                d["no_reply"] = True
                self._write_step(j, d, "agent")
        j.ended = True
        if j.path is not None:
            self._append(j, {"type": "end", "job": _safe_name(key), "ended": _iso(t), "t": round(t, 3),
                             "outcome": j.outcome or "unknown",
                             "outcome_source": "client" if j.outcome else "none",
                             "end_reason": scrub.scrub_text(why or "", 120),
                             "task": scrub.scrub_text(j.task, 2000) if j.task else None,
                             "note": scrub.scrub_text(j.note, 500) if j.note else None,
                             "steps": j.steps, "redacted_steps": j.redacted,
                             "dropped_events": self.rec.dropped})
        for tid in list(j.tabs):
            self.tab_job.pop(tid, None)
        self.jobs.pop(key, None)


_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def sweep(root, keep_days, max_bytes, now=None):
    """Delete recording days older than `keep_days`, then the oldest days until the
    whole folder is under `max_bytes`. Today is never deleted. Only YYYY-MM-DD
    folders directly under `root` are touched. Returns the days removed."""
    import shutil
    now = now or time.time()
    if not os.path.isdir(root):
        return []
    today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    cutoff = datetime.fromtimestamp(now - keep_days * 86400).strftime("%Y-%m-%d")
    days = sorted(d for d in os.listdir(root) if _DAY.match(d) and os.path.isdir(os.path.join(root, d))
                  and not os.path.islink(os.path.join(root, d)))
    gone = []
    for d in days:
        if d < cutoff and d != today:
            shutil.rmtree(os.path.join(root, d), ignore_errors=True)
            gone.append(d)
    days = [d for d in days if d not in gone]

    def size(d):
        total = 0
        for dp, _, fs in os.walk(os.path.join(root, d)):
            for f in fs:
                try:
                    total += os.lstat(os.path.join(dp, f)).st_size
                except OSError:
                    pass
        return total
    sizes = {d: size(d) for d in days}
    total = sum(sizes.values())
    for d in days:
        if total <= max_bytes:
            break
        if d == today:
            continue
        shutil.rmtree(os.path.join(root, d), ignore_errors=True)
        total -= sizes[d]
        gone.append(d)
    return gone


def _get(q, timeout):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def _mkdir_private(d):
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
