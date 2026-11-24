#!/usr/bin/env python3
"""Quiet session mode: one connection for a whole multi-step job, moving like a person.

Matt (2026-11-23), after the ineedhemp.com wp-login hCaptcha: every bb-click opened a
new lease, screenshotted before and after, and closed it again (~4 s a click). The page
flashed and repainted on each click, the mouse jumped straight onto each point, hCaptcha
turned down a correct answer, and bb-click said "nothing changed" for clicks inside the
captcha frame that did work. He keeps his captchas; the agent is his hands on his own
machines and solves them for him, quietly and fast.

    from quiet import quiet
    with quiet("ineedhemp-admin") as s:          # attach to that owner's open tab
        s.click(640, 410)                        # curved mouse path, then press/release
        s.click("Log In")                        # text or CSS selector works too
        print(s.changed())                       # sees changes inside cross-origin frames

    with quiet("job", url="https://example.com/login") as s:   # or open a hidden lease

In a quiet session nothing is screenshotted unless asked (s.shot()), the tab is never
brought forward, reloaded or re-focused, and the focus emulation of a hidden tab is set
once for the whole session instead of being flipped on every click.

Every Input.dispatchMouseEvent goes through the broker proxy like any other agent input,
so the human-is-working guard still applies.
"""
import base64, json, math, os, random, re, socket, struct, sys, time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from websocket import WebSocketTimeoutException

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import broker_client as bc  # noqa: E402

# Each broker answers its REST API on one port and proxies CDP on another.
# 9223/9221 = Matt's Brave (shown job tabs); 9233/9222 = the windowless Brave (hidden leases).
HIDDEN_BROKER = getattr(bc, "HIDDEN_BROKER", os.environ.get("BROKER_HIDDEN_URL", "http://127.0.0.1:9233"))
WINDOWLESS = getattr(bc, "WINDOWLESS", False)
BROKERS = [
    (bc.BROKER, os.environ.get("BROKER_CDP_PORT", "9221")),
    (HIDDEN_BROKER, os.environ.get("BROKER_HIDDEN_CDP_PORT", "9222")),
]


def post(path, payload, base=None):
    """POST to a broker's REST API (`base` picks which broker; default Matt's Brave)."""
    req = Request((base or bc.BROKER) + path, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=15) as r:
        return json.load(r)
STATE_DIR = os.path.join(os.environ.get("TMPDIR", "/tmp"), "bb-quiet")

LOGIN_URL = re.compile(r"(log-?in|sign-?in|wp-login|/auth|/session|signon|account/login)", re.I)
CAPTCHA_SRC = re.compile(r"(hcaptcha\.com|google\.com/recaptcha|recaptcha\.net|challenges\.cloudflare\.com)", re.I)

_CAPTCHA_IFRAME_JS = r"""(() => [...document.querySelectorAll('iframe')]
  .some(f => /hcaptcha\.com|google\.com\/recaptcha|recaptcha\.net|challenges\.cloudflare\.com/i.test(f.src || '')))()"""


def sensitive(tab, url=None):
    """Why this page should be driven quietly ('login page', 'captcha on page'), or None."""
    try:
        url = url or tab.eval("location.href") or ""
    except Exception:
        url = url or ""
    if LOGIN_URL.search(url or ""):
        return "login page"
    try:
        if tab.eval(_CAPTCHA_IFRAME_JS):
            return "captcha on page"
    except Exception:
        pass
    return None


# ---- finding the owner's tab on either broker --------------------------------------

def find_tab(owner):
    """(info, broker_url, cdp_port) of the open tab that belongs to `owner`, or None."""
    for base, port in BROKERS:
        try:
            with urlopen(base + "/peek?" + urlencode({"match": owner, "chars": 0}), timeout=20) as r:
                tabs = json.load(r)["tabs"]
        except Exception:
            continue
        mine = [t for t in tabs if t.get("leased_by") == owner or owner in (t.get("shown_by") or [])
                or t.get("child_owner") == owner
                or (len(owner) >= 8 and (t.get("target_id") or "").startswith(owner))]
        if mine:
            return mine[0], base, port
    return None


def _state_path(owner):
    return os.path.join(STATE_DIR, re.sub(r"[^A-Za-z0-9_.-]+", "_", owner) + ".json")


def load_state(owner):
    try:
        with open(_state_path(owner)) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(owner, **kv):
    os.makedirs(STATE_DIR, exist_ok=True)
    st = load_state(owner)
    st.update(kv)
    with open(_state_path(owner), "w") as f:
        json.dump(st, f)
    return st


def open_hidden(owner, url, purpose="quiet session", ttl=900):
    """Open a hidden lease tab that outlives this process (closed by close_hidden or its
    TTL), so several bb-session / bb-captcha calls can work in the same tab."""
    base = HIDDEN_BROKER if WINDOWLESS else bc.BROKER
    g = post("/lease", {"owner": owner, "url": url, "purpose": purpose, "hidden": True, "ttl": ttl},
                 base=base)
    save_state(owner, lease_id=g["lease_id"], broker=base, ttl=ttl, target_id=g.get("target_id"))
    return g


def close_hidden(owner):
    """Release the hidden lease open_hidden made (the tab closes). True when one was held."""
    st = load_state(owner)
    ok = False
    if st.get("lease_id"):
        try:
            post("/release", {"lease_id": st["lease_id"]}, base=st.get("broker"))
            ok = True
        except Exception:
            pass
    try:
        os.remove(_state_path(owner))
    except OSError:
        pass
    return ok


# ---- the session ---------------------------------------------------------------------

class QuietSession:
    """One held connection to one tab, with session-aware CDP (OOPIF child sessions)."""

    def __init__(self, tab, owner=None, shown=False, shots=False):
        self.tab, self.owner, self.shown, self.shots = tab, owner, shown, shots
        self.children = {}          # sessionId -> targetInfo (cross-origin iframes)
        self.worlds = {}            # (sessionId, frameId) -> executionContextId
        self.baseline = None
        st = load_state(owner) if owner else {}
        vw, vh = self._viewport()
        self.mx, self.my = st.get("mouse") or (random.uniform(0.35, 0.65) * vw, random.uniform(0.3, 0.6) * vh)

    # -- low level
    def send(self, method, session=None, timeout=None, **params):
        t = self.tab
        t._id += 1
        mid = t._id
        msg = {"id": mid, "method": method, "params": params}
        if session:
            msg["sessionId"] = session
        limit = timeout or t.step_timeout
        end = time.time() + limit
        t.ws.send(json.dumps(msg))
        while True:
            left = end - time.time()
            if left <= 0:
                raise bc.StepTimeout(f"{method} got no answer in {limit:.0f}s")
            t.ws.settimeout(left)
            try:
                raw = t.ws.recv()
            except (WebSocketTimeoutException, socket.timeout):
                raise bc.StepTimeout(f"{method} got no answer in {limit:.0f}s") from None
            if not raw:
                continue
            m = json.loads(raw)
            if m.get("id") == mid and m.get("sessionId") == session:
                if "error" in m:
                    err = m["error"].get("message") or ""
                    if "human is working in this tab" in err:
                        raise bc.HumanIsWorking(err + bc.ASK)
                    raise RuntimeError(err)
                return m.get("result", {})
            if "method" in m:
                self._event(m)

    def fire(self, method, session=None, **params):
        """Send without waiting for the reply (Chrome may hold mouseMoved replies in a background tab)."""
        t = self.tab
        t._id += 1
        msg = {"id": t._id, "method": method, "params": params}
        if session:
            msg["sessionId"] = session
        t.ws.send(json.dumps(msg))

    def _event(self, m):
        meth, p = m["method"], m.get("params") or {}
        if meth == "Target.attachedToTarget":
            self.children[p["sessionId"]] = p.get("targetInfo") or {}
        elif meth == "Target.detachedFromTarget":
            self.children.pop(p.get("sessionId"), None)
        elif meth == "Runtime.executionContextsCleared":
            sid = m.get("sessionId")
            for k in [k for k in self.worlds if k[0] == sid]:
                self.worlds.pop(k, None)
        self.tab.events.append(m)

    def pump(self, seconds=0.2):
        """Read events that arrived (frames attaching) for up to `seconds`."""
        end = time.time() + seconds
        while time.time() < end:
            self.tab.ws.settimeout(max(0.01, end - time.time()))
            try:
                raw = self.tab.ws.recv()
            except Exception:
                break
            if raw:
                m = json.loads(raw)
                if "method" in m:
                    self._event(m)

    def _viewport(self):
        try:
            r = json.loads(self.tab.eval("JSON.stringify([innerWidth, innerHeight])") or "[1280,800]")
            return float(r[0]), float(r[1])
        except Exception:
            return 1280.0, 800.0

    def start(self):
        # Cross-origin iframes (OOPIF) get their own sessions on this one socket.
        try:
            self.send("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=False, flatten=True)
        except Exception:
            pass
        if not self.shown:
            # Hidden tab: let it believe it has focus, ONCE for the whole session (never flipped per click).
            for meth, params in (("Emulation.setFocusEmulationEnabled", {"enabled": True}),
                                 ("Page.setWebLifecycleState", {"state": "active"})):
                try:
                    self.send(meth, **params)
                except Exception:
                    pass
        self.pump(0.3)
        self.mark()
        return self

    # -- frames
    def frames(self):
        """Every frame: [{session, frame_id, url, fragment}] (main session first, then OOPIFs)."""
        seen, main, kids = set(), [], []

        def walk(node, sid, out):
            f = node["frame"]
            if f["id"] not in seen:
                seen.add(f["id"])
                out.append({"session": sid, "frame_id": f["id"], "url": f.get("url", ""),
                            "fragment": f.get("urlFragment", ""), "parent": f.get("parentId")})
            for c in node.get("childFrames", []) or []:
                walk(c, sid, out)

        for sid in list(self.children):          # OOPIF frames are driven through their own session
            try:
                walk(self.send("Page.getFrameTree", session=sid)["frameTree"], sid, kids)
            except Exception:
                pass
        try:
            walk(self.send("Page.getFrameTree")["frameTree"], None, main)
        except Exception:
            pass
        return main + kids

    def frame_eval(self, frame, expr):
        """Evaluate `expr` in an isolated world of `frame` (the page cannot see it)."""
        key = (frame["session"], frame["frame_id"])
        for attempt in (0, 1):
            ctx = self.worlds.get(key)
            if ctx is None:
                ctx = self.send("Page.createIsolatedWorld", session=frame["session"], frameId=frame["frame_id"],
                                worldName="bb-quiet", grantUniveralAccess=True)["executionContextId"]
                self.worlds[key] = ctx
            try:
                r = self.send("Runtime.evaluate", session=frame["session"], expression=expr, contextId=ctx,
                              returnByValue=True, awaitPromise=True)
                return (r.get("result") or {}).get("value")
            except RuntimeError as e:
                self.worlds.pop(key, None)
                if attempt or "context" not in str(e).lower():
                    raise
        return None

    def owner_rect(self, frame):
        """Viewport rect of the <iframe> that holds `frame` in the top page, with visible flag."""
        try:
            n = self.send("DOM.getFrameOwner", frameId=frame["frame_id"])
            obj = self.send("DOM.resolveNode", backendNodeId=n["backendNodeId"])["object"]["objectId"]
            r = self.send("Runtime.callFunctionOn", objectId=obj, returnByValue=True, functionDeclaration=r"""
              function () { const r = this.getBoundingClientRect(); let v = true;
                for (let e = this; e; e = e.parentElement) { const cs = getComputedStyle(e);
                  if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) v = false; }
                const k = this.offsetWidth ? r.width / this.offsetWidth : 1;   // CSS transform scale
                return JSON.stringify({x: r.left + this.clientLeft * k, y: r.top + this.clientTop * k,
                  w: this.clientWidth * k, h: this.clientHeight * k, k, src: this.src || '',
                  visible: v && r.width > 20 && r.height > 20 && r.bottom > 0 && r.top < innerHeight
                           && r.right > 0 && r.left < innerWidth,
                  sx: scrollX, sy: scrollY}); }""")
            self.send("Runtime.releaseObject", objectId=obj)
            return json.loads(r["result"]["value"])
        except Exception:
            return None

    # -- change detection across every frame
    _MARK = r"""(() => { try { if (window.__bbqObs) window.__bbqObs.disconnect(); } catch (e) {}
      window.__bbqN = 0; window.__bbqObs = new MutationObserver(l => { window.__bbqN += l.length; });
      window.__bbqObs.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
      return location.href; })()"""
    _READ = "JSON.stringify({n: window.__bbqN || 0, url: location.href})"

    def mark(self):
        """Start counting changes in the page and every frame (call before a step)."""
        self.tab.events.clear()
        snap = {}
        for f in self.frames():
            try:
                snap[f["frame_id"]] = {"url": self.frame_eval(f, self._MARK), "host": _host(f["url"])}
            except Exception:
                pass
        try:
            snap["__title"] = self.tab.eval("document.title")
        except Exception:
            pass
        self.baseline = snap
        return snap

    def changed(self):
        """What changed since mark(): list of plain lines (empty = nothing)."""
        self.pump(0.05)
        out, base = [], self.baseline or {}
        now = {}
        for f in self.frames():
            fid = f["frame_id"]
            try:
                r = json.loads(self.frame_eval(f, self._READ) or "{}")
            except Exception:
                r = {}
            now[fid] = r
            label = "page" if not f.get("parent") and f["session"] is None else "frame " + _host(f["url"])
            b = base.get(fid)
            if b is None:
                out.append(f"new {label}: {f['url'][:100]}")
                continue
            if r.get("url") and b.get("url") and r["url"] != b["url"]:
                out.append(f"{label} url: {b['url'][:100]} -> {r['url'][:100]}")
            elif r.get("n"):
                out.append(f"{label} changed ({r['n']} DOM mutations)")
            elif not r:
                out.append(f"{label} reloaded")
        for fid, b in base.items():
            if fid != "__title" and fid not in now:
                out.append("frame %s went away" % b.get("host"))
        try:
            t = self.tab.eval("document.title")
            if base.get("__title") is not None and t != base["__title"]:
                out.append(f"title: {base['__title']} -> {t}")
        except Exception:
            pass
        return out

    # -- input that moves like a person
    def _path(self, x, y):
        x0, y0 = self.mx, self.my
        d = math.hypot(x - x0, y - y0)
        steps = max(6, min(28, int(d / 18) + random.randint(4, 8)))
        # one bowed control point off the straight line, side picked at random
        nx, ny = (-(y - y0) / d, (x - x0) / d) if d > 1 else (0, 0)
        bow = random.uniform(0.08, 0.25) * d * random.choice((-1, 1))
        cx, cy = (x0 + x) / 2 + nx * bow, (y0 + y) / 2 + ny * bow
        pts = []
        for i in range(1, steps + 1):
            t = i / steps
            t = t * t * (3 - 2 * t)                      # ease in and out
            px = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x
            py = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y
            j = 0 if i == steps else random.uniform(-1.2, 1.2)
            pts.append((px + j, py + j * random.uniform(-1, 1)))
        return pts

    def move(self, x, y):
        """Glide the mouse to (x, y) along a short curve, ~150-400 ms, with small jitter."""
        pts = self._path(x, y)
        total = random.uniform(0.15, 0.4)
        for px, py in pts:
            self.fire("Input.dispatchMouseEvent", type="mouseMoved", x=round(px, 1), y=round(py, 1),
                      button="none", buttons=0)
            time.sleep(total / len(pts) * random.uniform(0.6, 1.4))
        self.mx, self.my = x, y
        if self.owner:
            save_state(self.owner, mouse=[x, y])

    def click(self, target, jitter=2.0):
        """Human click on (x, y) viewport CSS px, or on text / a CSS selector in the top page.
        Returns {x, y, element}."""
        el = None
        if isinstance(target, (tuple, list)):
            x, y = float(target[0]), float(target[1])
        else:
            el = self.tab.find(target)
            if not el:
                raise LookupError(f"nothing visible matches {target!r}. Visible buttons: {self.tab.visible_labels()}")
            x, y = el["x"], el["y"]
            jitter = min(jitter * 2, max(1.0, min(el.get("w", 8), el.get("h", 8)) / 4))
        tx, ty = x + random.uniform(-jitter, jitter), y + random.uniform(-jitter, jitter)
        self.move(tx, ty)
        time.sleep(random.uniform(0.05, 0.16))
        self.send("Input.dispatchMouseEvent", type="mousePressed", x=round(tx, 1), y=round(ty, 1),
                  button="left", buttons=1, clickCount=1)
        time.sleep(random.uniform(0.05, 0.12))
        self.send("Input.dispatchMouseEvent", type="mouseReleased", x=round(tx, 1), y=round(ty, 1),
                  button="left", buttons=0, clickCount=1)
        return {"x": round(tx), "y": round(ty), "element": el}

    def drag(self, a, b):
        """Press at `a`, glide to `b` along a curve with the button held, release (drag-to-outline captchas)."""
        self.move(*a)
        time.sleep(random.uniform(0.08, 0.18))
        self.send("Input.dispatchMouseEvent", type="mousePressed", x=a[0], y=a[1], button="left", buttons=1,
                  clickCount=1)
        time.sleep(random.uniform(0.1, 0.2))
        for px, py in self._path(*b):
            self.fire("Input.dispatchMouseEvent", type="mouseMoved", x=round(px, 1), y=round(py, 1),
                      button="left", buttons=1)
            time.sleep(random.uniform(0.015, 0.04))
        self.mx, self.my = b
        time.sleep(random.uniform(0.08, 0.18))
        self.send("Input.dispatchMouseEvent", type="mouseReleased", x=b[0], y=b[1], button="left", buttons=0,
                  clickCount=1)
        if self.owner:
            save_state(self.owner, mouse=list(b))

    def clicks(self, points, gap=(0.25, 0.7)):
        """Several human clicks in a row with small pauses between them."""
        out = []
        for i, p in enumerate(points):
            if i:
                time.sleep(random.uniform(*gap))
            out.append(self.click(p))
        return out

    def type(self, text, per_key=(0.04, 0.14)):
        """Type into the focused field one character at a time (the text is never logged)."""
        for ch in text:
            if ch == "\n":
                self.key("Enter")
            else:
                self.send("Input.insertText", text=ch)
            time.sleep(random.uniform(*per_key))

    def key(self, name):
        codes = {"Enter": 13, "Tab": 9, "Escape": 27, "Backspace": 8, "ArrowDown": 40, "ArrowUp": 38}
        vk = codes.get(name, 0)
        extra = {"text": "\r"} if name == "Enter" else {}
        self.send("Input.dispatchKeyEvent", type="rawKeyDown", key=name, code=name,
                  windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)
        if extra:
            self.send("Input.dispatchKeyEvent", type="char", key=name, **extra)
        self.send("Input.dispatchKeyEvent", type="keyUp", key=name, code=name,
                  windowsVirtualKeyCode=vk, nativeVirtualKeyCode=vk)

    def wait_change(self, seconds=3.0):
        """Poll until something changes (any frame) or `seconds` pass; returns the change lines."""
        end = time.time() + seconds
        ch = []
        while time.time() < end:
            time.sleep(0.3)
            ch = self.changed()
            if ch:
                time.sleep(0.4)
                return self.changed()
        return ch

    def shot(self, path=None, clip=None):
        """Screenshot (whole viewport or `clip` = {x, y, w, h} in viewport CSS px). Only when asked."""
        params = {"format": "png"}
        if clip:
            sx, sy = json.loads(self.tab.eval("JSON.stringify([scrollX, scrollY])") or "[0,0]")
            params["clip"] = {"x": clip["x"] + sx, "y": clip["y"] + sy, "width": clip["w"], "height": clip["h"],
                              "scale": 1}
        png = base64.b64decode(self.send("Page.captureScreenshot", timeout=20, **params)["data"])
        path = path or bc._shot_path(self.owner or "quiet", "shot")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(png)
        return path, png_size(png)

    def url(self):
        return self.tab.eval("location.href")


def _host(url):
    m = re.match(r"https?://([^/#?]+)", url or "")
    return m.group(1) if m else (url or "")[:40]


def png_size(png):
    if png[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", png[16:24])
    return (0, 0)


class _Attached:
    """Attach to `owner`'s open tab for the whole session (one socket, one lease at most)."""

    def __init__(self, owner):
        hit = find_tab(owner)
        if not hit:
            raise LookupError(f"no open tab belongs to {owner}")
        info, base, port = hit
        self.info, self.base, self.lease_id = info, base, None
        if (info.get("human") or {}).get("active"):
            raise bc.HumanIsWorking("the human is working in that tab right now; not touching it." + bc.ASK)
        tid = info.get("target_id") or ""
        self.shown = bool(info.get("shown_by"))
        try:
            self.tab = bc.Tab(f"ws://127.0.0.1:{port}/devtools/page/{tid}")
            self.tab.eval("1")
        except Exception:
            g = post("/lease", {"owner": owner, "url": info.get("url"), "purpose": "quiet session",
                                    "ttl": 600}, base=base)
            if g.get("target_id") != tid:
                post("/release", {"lease_id": g["lease_id"]}, base=base)
                raise LookupError(f"could not attach to {owner}'s tab {tid[:12]}")
            self.lease_id = g["lease_id"]
            self.tab = bc.Tab(g["ws_url"])
        self.tab.owner = owner
        st = load_state(owner)
        if st.get("lease_id"):        # a hidden tab open_hidden made: keep its lease alive
            try:
                post("/renew", {"lease_id": st["lease_id"], "ttl": st.get("ttl", 900)}, base=st.get("broker"))
            except Exception:
                pass

    def close(self):
        self.tab.close()
        if self.lease_id:
            try:
                post("/release", {"lease_id": self.lease_id}, base=self.base)
            except Exception:
                pass


class quiet:
    """with quiet(owner) as s: ...  Attach to the owner's open tab (shown or hidden lease).
    With url=..., open a hidden lease for the block instead (released at the end)."""

    def __init__(self, owner, url=None, purpose="quiet session", shots=False, step_timeout=None):
        self.owner, self.url, self.purpose, self.shots, self.step_timeout = owner, url, purpose, shots, step_timeout
        self._cm = self._att = None

    def __enter__(self):
        if self.url:
            self._cm = bc.lease(self.owner, url=self.url, purpose=self.purpose, step_timeout=self.step_timeout)
            tab = self._cm.__enter__()
            time.sleep(1.5)
            shown = False
        else:
            self._att = _Attached(self.owner)
            tab, shown = self._att.tab, self._att.shown
            if self.step_timeout:
                tab.step_timeout = self.step_timeout
        return QuietSession(tab, owner=self.owner, shown=shown, shots=self.shots).start()

    def __exit__(self, *exc):
        if self._cm is not None:
            return self._cm.__exit__(*exc)
        if self._att is not None:
            self._att.close()
        return False


def quiet_click(tab, target, wait=3.0, label="click"):
    """Drop-in for Tab.click_trusted on login / captcha pages: same result shape, but no
    screenshots, no wake/focus flips, a curved mouse path, and frame-aware change detection."""
    s = QuietSession(tab, owner=getattr(tab, "owner", None), shown=True).start()
    t0 = time.time()
    r = s.click(target)
    changed = s.wait_change(wait)
    el = r["element"] or {"x": r["x"], "y": r["y"], "tag": "(point)", "text": ""}
    return {"changed": changed, "new_tabs": [], "http_errors": [], "how": "quiet", "x": r["x"], "y": r["y"],
            "element": el, "seconds": round(time.time() - t0, 1), "shots": [], "stuck": None}


# ---- captchas: the agent is Matt's hands on his own pages (2026-11-23) -----------------
# He keeps his captchas. The calling model reads the challenge picture and picks the
# targets; these helpers find the frames, crop the picture, click like a person, and say
# plainly whether the challenge cleared.

_TOKEN_JS = r"""(() => { const q = n => [...document.querySelectorAll('[name="' + n + '"]')].map(e => e.value).find(v => v) || '';
  return JSON.stringify({hcaptcha: q('h-captcha-response').length, recaptcha: q('g-recaptcha-response').length,
                         turnstile: q('cf-turnstile-response').length}); })()"""

_IN_FRAME = r"""(() => { const sel = %s; const el = sel.map(s => document.querySelector(s)).find(e => e);
  if (!el) return null; const r = el.getBoundingClientRect();
  return JSON.stringify({x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height,
    text: (el.innerText || el.getAttribute('aria-label') || el.value || '').trim().slice(0, 60),
    checked: el.getAttribute('aria-checked'), disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true'}); })()"""

_CHALLENGE_INFO = r"""(() => {
  const shown = e => { const r = e.getBoundingClientRect(); if (!(r.width > 0 && r.height > 0)) return false;
    for (let a = e; a; a = a.parentElement) { const cs = getComputedStyle(a);
      if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0 || a.getAttribute('aria-hidden') === 'true') return false; }
    return true; };
  const t = s => { const e = [...document.querySelectorAll(s)].find(shown); return e ? (e.innerText || '').trim().replace(/\s+/g, ' ') : ''; };
  const prompt = t('.prompt-text') || t('h2.prompt-text') || t('.rc-imageselect-desc-wrapper') || t('.rc-imageselect-desc-no-canonical') || t('h2');
  const err = t('.error-text') || t('.rc-imageselect-incorrect-response:not([style*="none"])') || t('.rc-imageselect-error-select-more:not([style*="none"])');
  const tiles = [...document.querySelectorAll('.task-image, .task, .rc-imageselect-tile')]
    .filter(e => { const r = e.getBoundingClientRect(); return r.width > 10 && r.height > 10; })
    .filter((e, i, a) => !a.some(o => o !== e && o.contains(e)))
    .map(e => { const r = e.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2,
      selected: e.getAttribute('aria-pressed') === 'true' || /selected/.test(e.className) || !!e.querySelector('.selected, .rc-imageselect-checkbox')}; });
  const b = document.querySelector('.button-submit, #recaptcha-verify-button');
  return JSON.stringify({prompt: prompt.slice(0, 200), error: err.slice(0, 160), tiles,
    button: b ? (b.innerText || b.getAttribute('aria-label') || '').trim() : ''}); })()"""

CHECKBOX_SEL = ["#checkbox", "#recaptcha-anchor", "input[type=checkbox]", "label"]
SUBMIT_SEL = [".button-submit", "#recaptcha-verify-button"]


def captcha_frames(s):
    """{'kind', 'checkbox': {frame, rect}, 'challenge': {frame, rect}} for the captcha on the page."""
    out = {"kind": None, "checkbox": None, "challenge": None}
    for f in s.frames():
        if not f.get("parent") and f["session"] is None:
            continue
        rect = s.owner_rect(f)
        src = ((rect or {}).get("src") or "") + " " + f["url"] + (f.get("fragment") or "")
        if not CAPTCHA_SRC.search(src):
            continue
        if "hcaptcha" in src:
            kind, role = "hcaptcha", ("challenge" if "frame=challenge" in src else
                                      "checkbox" if "frame=checkbox" in src else None)
        elif "challenges.cloudflare.com" in src:
            kind, role = "turnstile", "checkbox"
        else:
            kind, role = "recaptcha", ("challenge" if "/bframe" in src else "checkbox" if "/anchor" in src else None)
        if role and not out[role]:
            out["kind"] = out["kind"] or kind
            out[role] = {"frame": f, "rect": rect}
    return out


def captcha_status(s, cf=None):
    """Plain state: token present (cleared), checkbox checked, challenge open, prompt and error."""
    cf = cf or captcha_frames(s)
    st = {"kind": cf["kind"], "cleared": False, "checked": None, "challenge_open": False,
          "prompt": "", "error": "", "tiles": [], "button": ""}
    try:
        tok = json.loads(s.tab.eval(_TOKEN_JS) or "{}")
        st["cleared"] = any(v > 0 for v in tok.values())
    except Exception:
        pass
    if cf["checkbox"]:
        try:
            el = json.loads(s.frame_eval(cf["checkbox"]["frame"], _IN_FRAME % json.dumps(CHECKBOX_SEL[:2])) or "null")
            st["checked"] = (el or {}).get("checked") == "true"
        except Exception:
            pass
    ch = cf["challenge"]
    if ch and (ch["rect"] or {}).get("visible"):
        st["challenge_open"] = True
        try:
            info = json.loads(s.frame_eval(ch["frame"], _CHALLENGE_INFO) or "{}")
            k, ox, oy = ch["rect"].get("k", 1), ch["rect"]["x"], ch["rect"]["y"]
            st.update(prompt=info.get("prompt", ""), error=info.get("error", ""), button=info.get("button", ""),
                      tiles=[{"n": i + 1, "x": round(ox + t["x"] * k), "y": round(oy + t["y"] * k),
                              "selected": t["selected"]} for i, t in enumerate(info.get("tiles", []))])
        except Exception:
            pass
    st["cleared"] = st["cleared"] or bool(st["checked"])
    return st


def _point_in(s, part, sels):
    """Viewport point of the first element matching `sels` inside captcha frame `part`."""
    el = json.loads(s.frame_eval(part["frame"], _IN_FRAME % json.dumps(sels)) or "null")
    if not el:
        return None
    r = part["rect"]
    return r["x"] + el["x"] * r.get("k", 1), r["y"] + el["y"] * r.get("k", 1), el


def captcha_checkbox(s, wait=6.0):
    """Click the "I am human" box like a person, then wait for a pass or a challenge."""
    cf = captcha_frames(s)
    if not cf["checkbox"]:
        raise LookupError("no captcha checkbox on this page")
    p = _point_in(s, cf["checkbox"], CHECKBOX_SEL)
    if p is None:                                   # Turnstile's box sits in a closed shadow root
        r = cf["checkbox"]["rect"]
        p = (r["x"] + min(30, r["w"] / 4), r["y"] + r["h"] / 2, None)
    s.click((p[0], p[1]), jitter=3)
    return _settle(s, wait)


def captcha_shot(s, path=None):
    """Crop of the open challenge (or the checkbox when none is open): path, origin, scale."""
    cf = captcha_frames(s)
    part = cf["challenge"] if cf["challenge"] and (cf["challenge"]["rect"] or {}).get("visible") else cf["checkbox"]
    if not part or not part["rect"]:
        raise LookupError("no captcha frame on this page")
    r = part["rect"]
    clip = {"x": max(0, r["x"]), "y": max(0, r["y"]), "w": r["w"], "h": r["h"]}
    path, (pw, ph) = s.shot(path or bc._shot_path((s.owner or "captcha") + "-captcha", "challenge"), clip=clip)
    scale = pw / clip["w"] if clip["w"] else 1.0
    st = captcha_status(s, cf)
    info = {"path": path, "origin": [round(clip["x"], 1), round(clip["y"], 1)], "scale": round(scale, 4),
            "size": [pw, ph], "which": "challenge" if part is cf["challenge"] else "checkbox", **st}
    if s.owner:
        save_state(s.owner, shot={k: info[k] for k in ("origin", "scale", "size", "which")}, tiles=st["tiles"])
    return info


def to_page(owner, spec):
    """'x,y' (viewport CSS px), 'img:x,y' (pixels in the last captcha shot) or 't4' (tile 4) -> (x, y)."""
    spec = spec.strip()
    st = load_state(owner)
    if re.fullmatch(r"t\d+", spec, re.I):
        n = int(spec[1:])
        for t in st.get("tiles") or []:
            if t["n"] == n:
                return float(t["x"]), float(t["y"])
        raise LookupError(f"no tile {n} in the last captcha shot (run bb-captcha <owner> shot)")
    img = spec.lower().startswith("img:")
    a, b = (float(v) for v in spec.split(":", 1)[-1].split(","))
    if img:
        sh = st.get("shot")
        if not sh:
            raise LookupError("img: points need a captcha shot first")
        return sh["origin"][0] + a / sh["scale"], sh["origin"][1] + b / sh["scale"]
    return a, b


def captcha_submit(s, wait=8.0):
    """Click Verify / Next / Skip in the challenge, then report whether it cleared."""
    cf = captcha_frames(s)
    ch = cf["challenge"]
    if not ch or not (ch["rect"] or {}).get("visible"):
        st = captcha_status(s, cf)
        st["note"] = "no challenge is open"
        return st
    p = _point_in(s, ch, SUBMIT_SEL)
    if p is None:
        raise LookupError("no Verify/Next button in the challenge")
    before = captcha_status(s, cf)
    s.click((p[0], p[1]), jitter=3)
    st = _settle(s, wait, before)
    st["pressed"] = (p[2] or {}).get("text", "")
    return st


def _settle(s, wait, before=None):
    """Poll until the captcha clears, a challenge opens or changes, or `wait` s pass."""
    end = time.time() + wait
    st = None
    while time.time() < end:
        time.sleep(0.5)
        st = captcha_status(s)
        if st["cleared"]:
            break
        if before is None and st["challenge_open"]:
            time.sleep(1.0)          # let the pictures load before anyone looks
            st = captcha_status(s)
            break
        if before is not None and (st["prompt"] != before["prompt"] or st["error"] != before["error"]
                                   or not st["challenge_open"]):
            time.sleep(1.0)
            st = captcha_status(s)
            break
    return st
