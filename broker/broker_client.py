#!/usr/bin/env python3
"""Client for browser-broker. This is what agents import instead of touching CDP.

    from broker_client import lease

    with lease("yahoo-reaper", url="https://mail.yahoo.com", purpose="inbox sweep") as tab:
        tab.eval("document.title")

The context manager holds the lease, auto-renews it while you work, and releases
the tab when the block exits — including on an exception, which is the whole
point: a crashed agent must not strand a tab.

Never reach for cdp.Tab.find() from an agent again. find() lands you on whatever
tab happens to match, which may be the one the human is reading.
"""
import base64, collections, json, re, sys, threading, time
from contextlib import contextmanager
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from urllib.parse import urlencode

from websocket import create_connection

import os
BROKER = os.environ.get("BROKER_URL", "http://127.0.0.1:9223")
RENEW_EVERY = 60
TTL = 300


def _post(path, payload):
    req = Request(BROKER + path, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=15) as r:
        return json.load(r)


def status():
    with urlopen(BROKER + "/status", timeout=10) as r:
        return json.load(r)


class Tab:
    """A leased page. Same shape as cdp.Tab so agents port over easily."""

    def __init__(self, ws_url):
        self.ws = create_connection(ws_url, suppress_origin=True, timeout=30)
        self._id = 0
        self.target_id = ws_url.rstrip("/").rsplit("/", 1)[-1]
        self.events = collections.deque(maxlen=500)   # CDP events that arrived between replies

    def send(self, method, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message"))
                return msg.get("result", {})
            if "method" in msg and hasattr(self, "events"):
                self.events.append(msg)

    # ---- seeing and clicking like a person (2026-10-31) ----------------------
    # Matt, after an agent gave up on Apple's forum setup: "you can easily take a
    # screenshot, pinpoint exactly where it is on the screen, and press the button
    # within 10 seconds." Many sites ignore element.click() from a script: they act
    # only on event.isTrusted input, or need a user gesture to open a popup. A CDP
    # Input.dispatchMouseEvent is trusted, works in a hidden tab, and never needs
    # the tab brought to the front.

    def screenshot(self, path=None):
        """PNG of what the tab shows now (works hidden). Saved to `path` when given; returns the path or bytes."""
        png = base64.b64decode(self.send("Page.captureScreenshot", format="png")["data"])
        if path:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(png)
            return path
        return png

    def find(self, target):
        """Locate `target` (visible text, aria-label, or a CSS selector), scroll it into view
        and return {x, y, w, h, tag, text, covered_by} in viewport CSS pixels, or None."""
        r = self.eval(_FIND_JS % json.dumps(str(target)))
        return json.loads(r) if r else None

    def state(self):
        """Where the page is and what it looks like, cheaply: url, title, text length,
        signed-in / signed-out signals, HTTP status of the page itself, mutation count."""
        try:
            return json.loads(self.eval(_STATE_JS) or "{}")
        except Exception as e:      # mid-navigation: the old document is gone
            return {"error": str(e)}

    def _children(self):
        try:
            return {c.get("target"): c.get("url", "") for c in status().get("children", [])
                    if c.get("child_of") == self.target_id[:12]}
        except Exception:
            return {}

    def mouse_click(self, x, y):
        """A trusted left click at viewport CSS pixels (x, y). No focus change, no bringToFront."""
        # In a hidden tab Chrome holds the reply to mouseMoved ~5 s (it waits for a frame that a
        # background tab paints late). The move still lands in order, so send it without waiting.
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": "Input.dispatchMouseEvent",
                                 "params": {"type": "mouseMoved", "x": x, "y": y, "button": "none"}}))
        time.sleep(0.05)
        for kind, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
            self.send("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", buttons=buttons, clickCount=1)
            time.sleep(0.05)

    def _before(self, shots, label):
        try:
            self.send("Network.enable")
        except Exception:
            pass
        self.events.clear()
        self.eval(_MUT_JS)
        b = {"state": self.state(), "children": self._children(), "t": time.time()}
        if shots:
            b["shot"] = self.screenshot(_shot_path(label, "before"))
        return b

    def _after(self, before, wait, shots, label):
        """Wait up to `wait` s for the page to react; report what changed."""
        end = time.time() + wait
        st, kids = before["state"], before["children"]
        while True:
            time.sleep(0.3)
            st, kids = self.state(), self._children()
            moved = (st.get("url") != before["state"].get("url") or "error" in st
                     or (st.get("mut") or 0) > 0 or set(kids) - set(before["children"]))
            if moved or time.time() >= end:
                break
        if moved and time.time() < end:
            time.sleep(min(1.0, end - time.time()))   # let it settle before the after shot
            st, kids = self.state(), self._children()
        try:
            self.send("Runtime.evaluate", expression="1")     # pulls in the network events that arrived
        except Exception:
            pass
        errors = []
        for e in list(self.events):
            if e.get("method") == "Network.responseReceived":
                resp = (e.get("params") or {}).get("response") or {}
                if int(resp.get("status") or 0) >= 400 and (e["params"].get("type") in
                                                             ("Document", "XHR", "Fetch", None)):
                    errors.append({"status": resp.get("status"), "url": resp.get("url", "")[:200]})
        b = before["state"]
        changed = []
        if st.get("url") != b.get("url"):
            changed.append("url: %s -> %s" % (b.get("url"), st.get("url")))
        if st.get("title") != b.get("title") and st.get("title") is not None:
            changed.append("title: %s -> %s" % (b.get("title"), st.get("title")))
        if (st.get("mut") or 0) > 0:
            changed.append("page changed (%s DOM mutations)" % st["mut"])
        new_tabs = [{"target": t, "url": u} for t, u in kids.items() if t not in before["children"]]
        for t in new_tabs:
            changed.append("new tab opened: %s" % (t["url"] or "(blank)"))
        out = {"changed": changed, "new_tabs": new_tabs, "http_errors": errors, "before": b, "after": st,
               "seconds": round(time.time() - before["t"], 1)}
        if shots:
            out["shots"] = [before.get("shot"), self.screenshot(_shot_path(label, "after"))]
        out["stuck"] = self.stuck_check(out)
        return out

    def click_trusted(self, target, wait=3.0, shots=True, label="click"):
        """Screenshot, find `target` (text / selector, or an (x, y) tuple), trusted mouse click
        at its center, wait, screenshot again. Returns what changed (url, title, DOM, new tabs,
        HTTP errors, stuck verdict) plus the two screenshot paths."""
        before = self._before(shots, label)
        if isinstance(target, (tuple, list)):
            x, y = float(target[0]), float(target[1])
            el = {"x": x, "y": y, "tag": "(point)", "text": ""}
        else:
            el = self.find(target)
            if not el:
                raise LookupError(f"nothing visible on the page matches {target!r}")
            x, y = el["x"], el["y"]
        self.mouse_click(x, y)
        out = self._after(before, wait, shots, label)
        out.update({"how": "trusted", "x": round(x), "y": round(y), "element": el})
        return out

    def click(self, target, wait=3.0, shots=False, label="click"):
        """Script click first (element.click()); if the page does not react within `wait`
        seconds (same URL, no DOM change, no new tab), retry once as a trusted mouse click
        and log a line. Use click_trusted() directly when you already know the site needs it."""
        before = self._before(shots, label)
        hit = self.eval(_JS_CLICK % json.dumps(str(target)))
        if not hit:
            raise LookupError(f"nothing visible on the page matches {target!r}")
        out = self._after(before, wait, shots, label)
        if out["changed"]:
            out["how"] = "js"
            return out
        _log(f"js click on {target!r} at {before['state'].get('url')} did nothing in {wait:.0f}s; "
             f"retrying as a trusted mouse click")
        out = self.click_trusted(target, wait=wait, shots=shots, label=label)
        out["how"] = "js->trusted"
        return out

    def stuck_check(self, result=None):
        """Plain verdict when the tab looks stuck: blank, 401/403, or signed out while the
        human's own tab on the same site is signed in. None when nothing looks wrong."""
        st = (result or {}).get("after") or self.state()
        why = []
        if not st.get("error") and (st.get("text_len") or 0) < 20:
            why.append("the page is blank")
        for t in (result or {}).get("new_tabs", []):
            if t["url"] in ("", "about:blank"):
                why.append("the tab it opened is blank")
        codes = [e["status"] for e in (result or {}).get("http_errors", []) if e["status"] in (401, 403)]
        if st.get("http") in (401, 403):
            codes.append(st["http"])
        if codes:
            why.append("the site answered %s (not signed in for this request)" % sorted(set(codes)))
        if st.get("signed_out") and not st.get("signed_in"):
            why.append("this tab shows a Sign in link (signed out)")
        if not why:
            return None
        his = _his_session(st.get("url", ""))
        if his == "signed_in":
            why.append("your own tab on %s shows you signed in (session mismatch)" % _site(st.get("url", "")))
        return {"why": why, "his_tab": his, "url": st.get("url"),
                "fix": "redo this step visibly: bb-click --visible (needs --matt-ok, or the human idle 10+ min)"}

    def goto(self, url, wait=3):
        self.send("Page.enable")
        self.send("Page.navigate", url=url)
        time.sleep(wait)

    def eval(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True)
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


@contextmanager
def lease(owner, url=None, purpose=None, hidden=True, ttl=TTL):
    g = _post("/lease", {"owner": owner, "url": url, "purpose": purpose,
                         "hidden": hidden, "ttl": ttl})
    stop = threading.Event()

    def keepalive():
        # One failed renew used to end this thread for good, and the tab was
        # reaped out from under the agent a few minutes later. Keep trying; only
        # stop when the broker says the lease is gone.
        while not stop.wait(RENEW_EVERY):
            for attempt in range(5):
                try:
                    _post("/renew", {"lease_id": g["lease_id"], "ttl": ttl})
                    break
                except HTTPError as e:
                    if e.code == 404:
                        return
                except Exception:
                    pass
                if stop.wait(min(2 ** attempt, 15)):
                    return

    threading.Thread(target=keepalive, daemon=True).start()
    tab = None
    try:
        tab = Tab(g["ws_url"])
        yield tab
    finally:
        stop.set()
        if tab is not None:
            tab.close()
        try:
            _post("/release", {"lease_id": g["lease_id"]})
        except Exception:
            pass  # the reaper will collect it


def _log(line):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + line
    print(line, file=sys.stderr)
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "click.log"), "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _shot_path(label, when):
    d = os.environ.get("BB_SHOTS") or os.path.join(os.environ.get("TMPDIR", "/tmp"), "bb-click")
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:40]
    return os.path.join(d, "%s-%s-%s.png" % (safe, time.strftime("%H%M%S"), when))


def _site(url):
    from urllib.parse import urlparse
    h = (urlparse(url or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


_SIGNED_OUT = re.compile(r"(^|\n)\s*(sign in|log in|login|sign in / sign up)\s*($|\n)", re.I)
_SIGNED_IN = re.compile(r"\b(sign out|log out|logout|my account|your profile)\b", re.I)


def _his_session(url):
    """'signed_in' / 'signed_out' from the human's OWN tab on the same site (read only, via
    peek), or 'unknown' when he has none open."""
    site = _site(url)
    if not site:
        return "unknown"
    try:
        tabs = peek(site, chars=3000)
    except Exception:
        return "unknown"
    seen = "unknown"
    for t in tabs:
        if t.get("opened_by_broker") or t.get("leased_by") or t.get("shown_by"):
            continue
        txt = t.get("text") or ""
        if _SIGNED_IN.search(txt):
            return "signed_in"
        if _SIGNED_OUT.search(txt):
            seen = "signed_out"
    return seen


def human_idle_seconds():
    """Seconds since the human last touched this machine: the smaller of the OS idle
    time and the broker's last recorded input in any tab. None if neither is known."""
    vals = []
    try:
        with urlopen(BROKER + "/human", timeout=10) as r:
            v = json.load(r).get("last_input_seconds_ago")
            if v is not None:
                vals.append(float(v))
    except Exception:
        pass
    try:
        if sys.platform == "darwin":
            import subprocess
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"HIDIdleTime" = (\d+)', out)
            if m:
                vals.append(int(m.group(1)) / 1e9)
        elif os.name == "nt":
            import ctypes

            class LII(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
            li = LII(ctypes.sizeof(LII), 0)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
                vals.append((ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0)
    except Exception:
        pass
    return min(vals) if vals else None


# Finds the element a person would mean by `target`: a CSS selector if it is one,
# otherwise the smallest visible clickable thing whose text, aria-label, title or
# value matches (exact first, then contains). Scrolls it to the middle of the view.
_FIND_CORE = r"""
  const want = %s;
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const vis = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity !== 0; };
  let el = null;
  if (/^[#.\[]|^[a-z]+[#.\[:]|^\w+$/.test(want) && !/\s/.test(want.replace(/\[[^\]]*\]/g, ''))) {
    try { el = [...document.querySelectorAll(want)].find(vis) || null; } catch (e) {}
  }
  if (!el) {
    const w = norm(want);
    const sel = 'button,a,[role=button],[role=link],[role=menuitem],[role=tab],input[type=submit],input[type=button],summary,label,[onclick],[tabindex]';
    const all = [...document.querySelectorAll(sel)].filter(vis);
    const name = (e) => [e.innerText, e.getAttribute('aria-label'), e.getAttribute('title'), e.value].map(norm);
    el = all.find(e => name(e).includes(w)) || all.filter(e => name(e).some(n => n && n.includes(w)))
         .sort((a, b) => norm(a.innerText).length - norm(b.innerText).length)[0] || null;
  }
"""
_FIND_JS = "(() => {" + _FIND_CORE + r"""
  if (!el) return null;
  el.scrollIntoView({block: 'center', inline: 'center'});
  const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
  const top = document.elementFromPoint(x, y);
  const covered = top && top !== el && !el.contains(top) && !top.contains(el) ? top.tagName.toLowerCase() : null;
  return JSON.stringify({x, y, w: r.width, h: r.height, tag: el.tagName.toLowerCase(),
                         text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 80),
                         covered_by: covered});
})()"""
_JS_CLICK = "(() => {" + _FIND_CORE + r"""
  if (!el) return false;
  el.scrollIntoView({block: 'center'}); el.click(); return true;
})()"""
_MUT_JS = r"""(() => { try { if (window.__bbMutObs) window.__bbMutObs.disconnect(); } catch (e) {}
  window.__bbMut = 0;
  window.__bbMutObs = new MutationObserver((l) => { window.__bbMut += l.length; });
  window.__bbMutObs.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  return true; })()"""
_STATE_JS = r"""(() => { const t = (document.body && document.body.innerText) || '';
  const nav = performance.getEntriesByType('navigation')[0];
  const links = [...document.querySelectorAll('a,button,[role=button]')].map(e => (e.innerText || e.getAttribute('aria-label') || '').trim().toLowerCase());
  return JSON.stringify({url: location.href, title: document.title, text_len: t.trim().length,
    http: nav && nav.responseStatus || null, mut: window.__bbMut || 0,
    signed_out: links.some(s => /^(sign in|log in|login|sign in or create an account)$/.test(s)),
    signed_in: links.some(s => /^(sign out|log out|logout)$/.test(s)) || /\b(sign out|log out)\b/i.test(t.slice(0, 4000))});
})()"""


class HumanIsWorking(RuntimeError):
    """The human is typing or clicking in that tab right now. Do not reload, reopen or
    close it. `page` is what it shows at this moment: continue from there."""

    def __init__(self, message, page=None):
        super().__init__(message)
        self.page = page or {}


class StopAndAsk(RuntimeError):
    """You have opened and closed this page too many times. Stop and ask the human
    in one line what is wrong. Retrying is what this exists to prevent."""


def show(url, owner=None, purpose=None, wait=None, dwell=None, reload=False):
    """Put a page in front of the human, taking turns with every other agent.

    Use this instead of `open -a "Brave Browser" <url>` or AppleScript activate.
    Those go straight to macOS, around the proxy, and two agents doing it minutes
    apart is what throws his window back and forth. This queues.

    `owner` is your job. The job owns one tab on one site until unshow(owner).
    Showing the same site again returns that tab AS IS (reply["already_open"]),
    with reply["page"] holding what it shows now: read it before acting. It is
    reloaded only with reload=True, and never while the human works in it.

    Raises RuntimeError if the screen was busy for longer than `wait` (print the
    URL and move on), HumanIsWorking (continue from e.page) or StopAndAsk.
    """
    body = {"owner": owner or os.environ.get("BROKER_OWNER") or "anonymous", "url": url,
            "purpose": purpose}
    if wait is not None:
        body["wait"] = wait
    if dwell is not None:
        body["dwell"] = dwell
    if reload:
        body["reload"] = True
    try:
        return _post("/show", body)
    except HTTPError as e:
        if e.code not in (409, 423, 429):
            raise
        d = json.loads(e.read() or b"{}")
        if e.code == 409:
            raise RuntimeError(
                f"screen busy: {d.get('held_by')} is showing {d.get('showing')} "
                f"for another {d.get('seconds_left')}s") from None
        if e.code == 423:
            raise HumanIsWorking(d.get("error", "the human is working in that tab"), d.get("page")) from None
        if e.code == 429:
            raise StopAndAsk(d.get("error", "stop and ask the human")) from None


def peek(match, chars=400):
    """Read-only state of every open tab matching `match` (a job owner, a lease owner,
    a target id, or part of a URL): url, title, text, filled fields (secrets masked)
    and human = {active, human_active_since, last_input_seconds_ago, inputs}.
    Call it before every step on a page the human might touch."""
    with urlopen(BROKER + "/peek?" + urlencode({"match": match, "chars": chars}), timeout=30) as r:
        return json.load(r)["tabs"]


def human_active(match):
    """True when the human is working, right now, in any tab matching `match`."""
    return any((t.get("human") or {}).get("active") for t in peek(match, chars=0))


def unshow(match):
    """Close the page(s) you put up with show(), by owner or URL substring.

    Rule: close every tab you opened the moment its task is done. Only tabs the
    broker opened are closed; the human's own tabs, and any tab he is working in
    right now, are left to him (reply["left_open"] says which and why)."""
    return _post("/unshow", {"match": match})


def stage():
    """Who has the screen right now, and what is on it."""
    with urlopen(BROKER + "/stage", timeout=10) as r:
        return json.load(r)
