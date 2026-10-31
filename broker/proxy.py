#!/usr/bin/env python3
"""browser-broker proxy — a transparent Chrome DevTools Protocol endpoint that
enforces tab ownership for EVERY client, with zero changes to any of them.

The real browser runs on an upstream port (default 9229). This process
listens on the port every tool already expects (default 9222) and speaks
CDP back at them:

    agent-browser --cdp 9222, chrome-devtools-mcp --browser-url, Playwright's
    connectOverCDP, Puppeteer.connect, browser-use, a curl to /json, or a raw
    websocket — all unchanged.

What each of them gets is a browser in which the ONLY tabs that exist are the
ones this client was given. A tool that "grabs the first page" grabs its own
leased page, because that is the only page it can see. The human's tabs, and
every other agent's tabs, are simply not there.

Identity comes for free from the connection:
  * a browser-level websocket (/devtools/browser/...) is one client for as
    long as it stays connected; its tabs are released when it goes away.
  * a legacy HTTP client (GET /json, PUT /json/new, then a page websocket)
    claims a tab by attaching to it; the tab is released when the page socket
    closes. A pool tab is handed to one /json caller and one page socket only.
  * the REST lease API on 9223 (broker_client.py) still works and is the way
    to hold a tab across reconnects, with a TTL.

Focus-stealing commands (Target.activateTarget, Page.bringToFront,
/json/activate) are answered with success and never forwarded, so no agent
can bring itself in front of the human. Agent tabs are ordinary background
tabs in the window that is already open — they never take focus, and they
keep rendering because the browser is launched with backgrounding disabled
(launch-browser.sh).

Ownership rule, deliberately dumb: this proxy can only hand out tabs it
created. It never enumerates, attaches to, or closes anything else.

Browser-wide commands that would hit the human (Browser.close, Browser.crash,
Browser.setWindowBounds) are swallowed too. Puppeteer's browser.close() sends
Browser.close, and through a plain CDP port that quits the human's whole browser.
"""
import asyncio, concurrent.futures, json, os, stat, sys, time, uuid, threading, subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import websockets, logging
if os.environ.get("BROKER_DEBUG"):
    logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")
else:
    # Handshake failures from port probes printed a full traceback each, and grew
    # launchd.err to 28 MB on the M5 (2026-10-06). They are noise, not errors.
    logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve
from websockets.http11 import Response
from websockets.datastructures import Headers

UPSTREAM = int(os.environ.get("BROKER_UPSTREAM_PORT", 9229))   # the real browser
LISTEN = int(os.environ.get("BROKER_PROXY_PORT", 9222))          # what tools dial
API = int(os.environ.get("BROKER_PORT", 9223))                   # REST lease API
DEFAULT_TTL = int(os.environ.get("BROKER_TTL", 300))
UNATTACHED_TTL = 60          # a tab handed to a legacy client that never attached
HEARTBEAT_EVERY = int(os.environ.get("BROKER_HEARTBEAT", 3600))
LOG = os.path.expanduser(os.environ.get("BROKER_LOG",
      os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker.log")))
FOCUS_STEALERS = {"Target.activateTarget", "Page.bringToFront"}
# Commands that act on the human's browser as a whole. Answered with success and
# never forwarded. Browser.close is the one that bit: Puppeteer's browser.close()
# sends it, and it quit Matt's whole Brave, every window and tab.
BROWSER_KILLERS = {"Browser.close", "Browser.crash", "Browser.crashGpuProcess",
                   "Browser.setWindowBounds"}
LOG_MAX = 5 * 1024 * 1024          # broker.log rolls to broker.log.1 past this
STDIO_MAX = 10 * 1024 * 1024       # launchd.err / proxy.err get emptied past this
STATE = os.path.join(os.path.dirname(os.path.abspath(LOG)), "tabs.json")
POOL_HOLD = 10                     # seconds a listed pool tab is reserved for its /json caller
BROWSER_APP = os.environ.get("BROWSER_APP", "Brave Browser")   # for the one allowed activate
STAGE_DWELL = int(os.environ.get("BROKER_STAGE_DWELL", 20))    # seconds a shown page is protected
STAGE_WAIT = int(os.environ.get("BROKER_STAGE_WAIT", 30))      # seconds a caller will queue
# The macOS app-activation step of the stage. The test suite turns it off so a
# test run never brings a real window forward on the machine running it.
STAGE_ACTIVATE_APP = os.environ.get("BROKER_STAGE_ACTIVATE_APP", "1") != "0"
# The five rules (2026-10-23). See "When the human helps" in the README.
HUMAN_QUIET = int(os.environ.get("BROKER_HUMAN_QUIET", 20))     # seconds his last input protects a tab
AGENT_ECHO = 1.5                   # trusted events this soon after an agent's own CDP input are the agent's
# 2026-10-25: an agent's scripted typing (Runtime.evaluate with focus(), execCommand
# insertText, value setters) fires trusted input/change events, and a textarea's
# change can come seconds later when focus moves. input/change this long after an
# agent's last command in a tab, with no real key, click or paste from him since,
# are the agent's. Kept narrow: Mac dictation types with input events and no keydown.
AGENT_ACTIVE = int(os.environ.get("BROKER_AGENT_ACTIVE", 30))
AGENT_TOUCH = ("Runtime.evaluate", "Runtime.callFunctionOn", "DOM.focus")   # recorded, never blocked
GENUINE = ("pointerdown", "keydown", "paste")   # inputs only a person makes; they always count
CHURN_WINDOW = int(os.environ.get("BROKER_CHURN_WINDOW", 600))  # seconds an open/close cycle is remembered
CHURN_LIMIT = int(os.environ.get("BROKER_CHURN_LIMIT", 2))      # cycles allowed before "stop and ask"
PORT_GRACE = 8                     # seconds a restarting proxy waits for the old one to let go


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        if os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
    except OSError:
        pass
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# upstream browser connection (the proxy's own control channel)
# ---------------------------------------------------------------------------
class Upstream:
    def __init__(self):
        self.ws = None
        self._id = 0
        self._pending = {}
        self._lock = asyncio.Lock()
        self.version = {}

    async def _http(self, path):
        import urllib.request
        def get():
            with urllib.request.urlopen(f"http://127.0.0.1:{UPSTREAM}{path}", timeout=10) as r:
                return json.load(r)
        return await asyncio.to_thread(get)

    async def connect(self):
        self.version = await self._http("/json/version")
        self.ws = await ws_connect(self.version["webSocketDebuggerUrl"], max_size=None)
        asyncio.create_task(self._reader())
        # 2026-10-29: hear about every new tab, so one that a broker tab opens by
        # itself (target=_blank, window.open, an OAuth popup) is known as that
        # tab's child and closed with it. The reply needs nobody waiting on it,
        # and send() cannot be used here: it holds the lock that called us.
        self._id += 1
        await self.ws.send(json.dumps({"id": self._id, "method": "Target.setDiscoverTargets",
                                       "params": {"discover": True}}))

    async def _reader(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                if "id" not in m:
                    if REG is not None and m.get("method", "").startswith("Target."):
                        try:
                            REG.on_target_event(m["method"], m.get("params") or {})
                        except Exception as e:
                            log(f"target event {m.get('method')} not handled: {type(e).__name__}: {e}")
                    continue
                fut = self._pending.pop(m.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(m)
        except Exception as e:
            log(f"upstream control socket dropped: {e}")
        self.ws = None
        # Anyone waiting on a reply would otherwise sit out the full 20s timeout.
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("browser control socket dropped"))

    async def send(self, method, **params):
        async with self._lock:
            if self.ws is None:
                await self.connect()
        self._id += 1
        mid = self._id
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            m = await asyncio.wait_for(fut, 20)
        finally:
            self._pending.pop(mid, None)
        if "error" in m:
            raise RuntimeError(m["error"].get("message"))
        return m.get("result", {})

    async def alive(self):
        try:
            await self.send("Browser.getVersion")
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# registry: which targets we made, who holds them
# ---------------------------------------------------------------------------
class Registry:
    def __init__(self, up):
        self.up = up
        self.tabs = {}      # target_id -> dict(owner, kind, hidden, created, expires, attached, lease_id)
        self.children = {}  # target_id -> dict(owner, parent, created, url): tabs a broker tab opened itself
        self.loop = None

    def _save(self):
        """Write down which tabs are ours, so a proxy that crashed or restarted
        can close them next time instead of leaving them in Matt's window with
        nobody owning them."""
        try:
            tmp = STATE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(list(self.tabs), f)
            os.replace(tmp, STATE)
        except OSError:
            pass

    async def close_leftovers(self):
        """Startup: close tabs a previous run of this proxy made and never closed.
        Only IDs from our own record are touched, never anything else."""
        try:
            with open(STATE) as f:
                old = set(json.load(f))
        except (OSError, ValueError):
            return
        try:
            live = {t["targetId"] for t in (await self.up.send("Target.getTargets")).get("targetInfos", [])}
        except Exception:
            return
        closed = 0
        for tid in old & live:
            # 2026-10-23: a broker restarted mid-task closed a tab the human had
            # been working in. The listener's record lives in the page itself, so
            # it outlasts the broker: a leftover he ever typed in is his now.
            try:
                touched = bool(json.loads(await page_eval(tid, "JSON.stringify("
                               "(window.__bbHuman && window.__bbHuman.events) || [])", 3) or "[]"))
            except Exception:
                touched = False
            if touched:
                log(f"left tab {tid[:12]} from the last run open: the human worked in it")
                continue
            try:
                await self.up.send("Target.closeTarget", targetId=tid)
                closed += 1
            except Exception:
                pass
        if closed:
            log(f"closed {closed} tab(s) left over from the last run")
        self._save()

    async def create(self, owner, url, hidden=True, kind="rest", ttl=None, context=None):
        # A plain background tab in the window that is already open. Matt asked
        # for this on 2026-09-07: parking agent work in its own off-screen window
        # shoved his browser aside every time an agent ran. background=True means
        # it opens behind whatever he is looking at and never takes focus, and
        # the ownership filter below -- not the window -- is what keeps agents
        # out of each other's tabs, so nothing about the isolation changes.
        args = {"url": url or "about:blank", "background": True}
        if context:
            args["browserContextId"] = context
        res = await self.up.send("Target.createTarget", **args)
        tid = res["targetId"]
        # Register ownership BEFORE parking the window: Chrome tells auto-attached
        # clients about the new target immediately, and their filter must be
        # able to see that it is theirs.
        self.tabs[tid] = {"owner": owner, "kind": kind, "hidden": hidden,
                          "created": time.time(), "attached": 0,
                          "expires": time.time() + (ttl or UNATTACHED_TTL),
                          "lease_id": uuid.uuid4().hex[:12] if kind == "rest" else None,
                          "listed_until": 0}
        self._save()
        HUMANS.watch(tid)
        log(f"tab {tid[:12]} -> {owner} ({kind}{', hidden' if hidden else ''})")
        return tid

    async def adopt(self, tid, owner, ttl):
        """Lease an existing job tab instead of opening a copy of it (rule 1).

        The tab stays the job's: releasing this lease never closes it, bb-close does."""
        self.tabs[tid] = {"owner": owner, "kind": "rest", "hidden": False, "created": time.time(),
                          "attached": 1, "expires": time.time() + ttl,
                          "lease_id": uuid.uuid4().hex[:12], "listed_until": 0, "shared": True}
        self._save()
        HUMANS.watch(tid)
        log(f"tab {tid[:12]} -> {owner} (lease on its own shown job tab, no copy opened)")
        return tid

    def owned_by(self, owner):
        return [t for t, i in self.tabs.items() if i["owner"] == owner]

    # ---- child tabs (2026-10-29) ---------------------------------------------
    # A DevHunt listing tab stayed open in Matt's browser after an agent reported
    # "tabs closed": submitting the form in its leased tab made the SITE open a
    # new tab (target=_blank), which the broker never opened and so never knew
    # about. Chrome names the tab that opened a page (targetInfo.openerId, set even
    # for noopener links). A page whose opener is one of ours is ours too, owned by
    # the same job, and it goes when its parent goes, under the parent's rules.
    # A tab whose opener is not ours (his own tabs) is never recorded.
    def owner_of(self, tid):
        """The owner a tab the broker controls belongs to: a lease or client tab,
        a child of one, or a job tab on screen. None for anything else."""
        if tid in self.tabs:
            return self.tabs[tid]["owner"]
        if tid in self.children:
            return self.children[tid]["owner"]
        if STAGE is not None and tid in STAGE.created:
            return STAGE.job_of(tid)           # a job shown on his own tab does not make its children ours
        return None

    def note_child(self, info):
        """Record `info` (a targetInfo) as a child tab if one of our tabs opened it."""
        tid, opener = info.get("targetId"), info.get("openerId")
        if (info.get("type") != "page" or not tid or not opener or tid == opener
                or tid in self.tabs or tid in self.children):
            return False
        owner = self.owner_of(opener)
        if owner is None:
            return False                       # opened from a tab that is not ours: his
        self.children[tid] = {"owner": owner, "parent": opener, "created": time.time(),
                              "url": info.get("url", "")}
        HUMANS.watch(tid)
        log(f"tab {tid[:12]} -> {owner} (opened by its tab {opener[:12]}, a child tab)")
        return True

    def on_target_event(self, method, p):
        """Target.* events on the proxy's own control socket."""
        if method == "Target.targetCreated":
            self.note_child(p.get("targetInfo") or {})
        elif method == "Target.targetInfoChanged":
            info = p.get("targetInfo") or {}
            if info.get("targetId") in self.children:
                self.children[info["targetId"]]["url"] = info.get("url", "")
            else:
                self.note_child(info)          # Chrome may fill in the opener late
        elif method == "Target.targetDestroyed":
            if self.children.pop(p.get("targetId"), None) is not None:
                HUMANS.forget(p.get("targetId"))

    async def scan_children(self):
        """Pick up child tabs whose targetCreated event this proxy missed (its
        control socket reconnected, or the tab came up before it was listening)."""
        try:
            infos = (await self.up.send("Target.getTargets")).get("targetInfos", [])
        except Exception:
            return
        while any([self.note_child(i) for i in infos]):
            pass                               # grandchildren need their parent recorded first

    def children_of(self, parent):
        return [c for c, i in self.children.items() if i["parent"] == parent]

    def _opened_by_him(self, child, info):
        """True when the human's own click in the parent opened this tab (real input
        in the parent just before it appeared). Agent input is already discounted."""
        t = info["created"]
        return any(t - 3 <= e <= t + 1 for e, _ in HUMANS.events.get(info["parent"], ()))

    async def close_children(self, parent, why=""):
        """Close every child tab of `parent`, grandchildren included, under the same
        rules as a lease tab: one the human is working in, worked in, or opened
        himself is left open and handed to him. Returns (closed, left_open)."""
        closed, left = [], []
        for c in self.children_of(parent):
            kc, kl = await self.close_child(c, why)
            closed += kc
            left += kl
        return closed, left

    async def close_child(self, c, why=""):
        """Close one child tab and its own children, or hand it to the human."""
        info = self.children.pop(c, None)
        if info is None:
            return [], []
        parent = info["parent"]
        entry = {"owner": info["owner"], "target": c[:12], "child_of": parent[:12],
                 "url": info.get("url", "")}
        his = "he opened it himself" if self._opened_by_him(c, info) else \
            await self._his_now(c, {"created": info["created"]})
        if his:
            sub_left = self.leave_children(c, his)
            HUMANS.forget(c)
            log(f"child tab {c[:12]} left open for the human ({info['owner']}, {why or 'released'}): {his}")
            return [], [dict(entry, why=his)] + sub_left
        closed, left = await self.close_children(c, why)
        HUMANS.forget(c)
        try:
            await self.up.send("Target.closeTarget", targetId=c)
        except Exception:
            pass
        log(f"child tab {c[:12]} closed ({info['owner']}, opened by {parent[:12]}"
            f"{', ' + why if why else ''})")
        return closed + [entry], left

    def leave_children(self, parent, why):
        """Hand `parent`'s child tabs to the human with it: forget them, close nothing."""
        left = []
        for c in self.children_of(parent):
            info = self.children.pop(c, None)
            if info is None:
                continue
            left += self.leave_children(c, why)
            HUMANS.forget(c)
            left.append({"owner": info["owner"], "target": c[:12], "child_of": parent[:12],
                         "url": info.get("url", ""), "why": why})
        if left:
            log(f"{len(left)} child tab(s) of {parent[:12]} left open with it: {why}")
        return left

    def free_pool(self):
        """Tabs made for legacy /json clients that nobody has attached to yet."""
        now = time.time()
        return [t for t, i in self.tabs.items()
                if i["kind"] == "pool" and i["attached"] == 0 and i["listed_until"] <= now]

    async def close(self, tid, why="", report=None):
        """Close a tab the broker made, and the child tabs it opened. True if it closed.

        Two exceptions, and both leave the tab open: it is also an open job tab
        on the human's screen (bb-close finishes that), or the human is working
        in it right now (rule 4: it is his, and closing it would wipe his work).
        Its children follow it: kept with a job tab, handed to him with his tab.
        `report`, a dict, gets the children's "children_closed" / "children_left_open"."""
        if tid not in self.tabs:
            return False
        await self.scan_children()             # while the parent is still ours, so its children resolve
        info = self.tabs.pop(tid, None)
        if info is None:
            return False
        self._save()
        kids_closed, kids_left = [], []
        try:
            if STAGE is not None and STAGE.job_of(tid):
                log(f"tab {tid[:12]} lease ended ({info['owner']}, {why or 'released'}); kept open, "
                    f"it is the shown job tab of {STAGE.job_of(tid)}")
                return False
            his = await self._his_now(tid, info)
            if his:
                kids_left = self.leave_children(tid, his)
                HUMANS.forget(tid)
                log(f"tab {tid[:12]} left open for the human ({info['owner']}, {why or 'released'}): {his}")
                return False
            kids_closed, kids_left = await self.close_children(tid, why)
            HUMANS.forget(tid)
            try:
                await self.up.send("Target.closeTarget", targetId=tid)
            except Exception:
                pass
            log(f"tab {tid[:12]} closed ({info['owner']}{', ' + why if why else ''})")
            return True
        finally:
            if report is not None:
                report["children_closed"], report["children_left_open"] = kids_closed, kids_left

    async def _his_now(self, tid, info):
        """Why tab `tid` now belongs to the human, or None if the broker may close it.

        2026-10-25: a hidden lease tab came forward when an agent closed the tab in
        front of it, he clicked in it, and 27s later (past HUMAN_QUIET) the lease
        released and the tab vanished under him. So: any real input from him since
        the lease began makes it his, and so does a hidden lease that is on screen
        now (he switched to it). Visible-by-design leases close as before.

        2026-10-29: the visibility half is gone. A LinkedIn post from a hidden lease was
        left open "for the human" with no input from anyone: a background tab opens right
        beside the tab he is on, so it comes on screen whenever that tab closes, or when
        the page focuses itself after an agent's click. Being on screen proves nothing
        about him; only his real input (a key, click, paste or scroll) makes a tab his."""
        if HUMANS.active(tid):
            return "he is working in it"
        try:
            v = json.loads(await asyncio.wait_for(page_eval(tid, CLOSE_CHECK_JS, 2), 3) or "{}")
        except Exception:
            v = {}
        if not isinstance(v, dict):
            v = {}
        HUMANS.merge(tid, v.get("human"))
        if HUMANS.active(tid):
            return "he is working in it"
        if HUMANS.touched_since(tid, info.get("created", 0)):
            return "he worked in it during the lease"
        if info.get("hidden") and v.get("visibility") == "visible":
            log(f"tab {tid[:12]} is on screen but got no input from the human: closing it as the agent's")
        return None

    async def release_owner(self, owner, why="client disconnected"):
        for tid in self.owned_by(owner):
            await self.close(tid, why)

    async def reap(self):
        now = time.time()
        for tid, i in list(self.tabs.items()):
            if i["kind"] == "ws":
                continue                       # bound to a live connection
            if i["kind"] == "rest" and i["attached"]:
                if i["expires"] <= now:
                    await self.close(tid, "lease expired")
            elif i["attached"] == 0 and i["expires"] <= now:
                await self.close(tid, "never attached")
        # drop bookkeeping for tabs that vanished under us (browser restart)
        try:
            live = {t["targetId"] for t in (await self.up.send("Target.getTargets")).get("targetInfos", [])}
        except Exception:
            return
        gone = [tid for tid in self.tabs if tid not in live]
        for tid in gone:
            self.tabs.pop(tid, None)
            HUMANS.forget(tid)
        if gone:
            self._save()
        for tid in [c for c in self.children if c not in live]:
            self.children.pop(tid, None)
            HUMANS.forget(tid)

    def status(self):
        now = time.time()
        return {"tabs": [{"target": t[:12], "owner": i["owner"], "kind": i["kind"],
                          "hidden": i["hidden"], "attached": i["attached"],
                          "expires_in": round(i["expires"] - now, 1) if i["kind"] != "ws" else None,
                          "lease_id": i["lease_id"], "children": self.child_list(t)}
                         for t, i in self.tabs.items()],
                "count": len(self.tabs),
                "children": [{"target": c[:12], "owner": i["owner"], "child_of": i["parent"][:12],
                              "url": i.get("url", ""), "open_for": round(now - i["created"]),
                              "human": HUMANS.info(c)} for c, i in self.children.items()]}

    def child_list(self, parent):
        """`parent`'s child tabs, grandchildren included, for /status and /stage."""
        out = []
        for c in self.children_of(parent):
            out.append({"target": c[:12], "url": self.children[c].get("url", "")})
            out += self.child_list(c)
        return out

    def target_info(self, tid):
        """Shape a /json entry for a tab we own."""
        return {"id": tid, "type": "page", "title": "", "url": "",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{LISTEN}/devtools/page/{tid}",
                "devtoolsFrontendUrl": f"/devtools/inspector.html?ws=127.0.0.1:{LISTEN}/devtools/page/{tid}"}



# ---------------------------------------------------------------------------
# the human: notice when he is working in a tab (rules 2-4, 2026-10-23)
# ---------------------------------------------------------------------------
# Matt, 2026-10-23: agents opened a tab, started a form, closed it, opened it again
# "over 10 times right before my eyes". When he typed in the form or solved the
# captcha himself, the agent never noticed: it reloaded or re-showed the page and
# wiped his work. Everything below exists so the broker can tell, in code, that a
# person is using a tab, and refuse to pull it out from under him.

# Injected into every tab the broker opens or shows. It records trusted input
# only (event.isTrusted: a real keyboard or mouse; a page script cannot fake it)
# and reports it two ways: a window variable any reader can poll, and a CDP
# binding that pushes each event to the broker the moment it happens, which
# also survives the page navigating away (a submitted form). Focus moving into
# an iframe counts too, because a cross-site captcha frame never lets its own
# clicks bubble up to the page. CDP Input events an agent sends are trusted as
# well; the broker sees those go through and discounts them (AGENT_ECHO).
HUMAN_BINDING = "__bbHumanEvent"
HUMAN_JS = r"""(() => {
  if (window.__bbHuman) return 'already';
  const s = {v: 1, events: []};
  try { Object.defineProperty(window, '__bbHuman', {value: s}); } catch (e) { return 'blocked'; }
  let last = 0;
  const send = (kind) => {
    const t = Date.now() / 1000;
    if (t - last < 0.25) return;
    last = t;
    s.events.push([t, kind]);
    if (s.events.length > 50) s.events.shift();
    try { if (typeof window.__bbHumanEvent === 'function') window.__bbHumanEvent(JSON.stringify([t, kind])); } catch (e) {}
  };
  const note = (e) => { if (e.isTrusted) send(e.type); };
  for (const k of ['pointerdown', 'keydown', 'input', 'paste', 'change', 'wheel']) addEventListener(k, note, {capture: true, passive: true});
  addEventListener('blur', (e) => {
    if (!e.isTrusted || e.target !== window) return;
    setTimeout(() => { const a = document.activeElement; if (a && a.tagName === 'IFRAME') send('frame'); }, 0);
  }, true);
  return 'installed';
})()"""

# Read at release time: is this tab the one on screen, and what did the page record?
CLOSE_CHECK_JS = r"""JSON.stringify({visibility: document.visibilityState,
  human: (window.__bbHuman && window.__bbHuman.events) || []})"""


def page_key(url):
    """What "the same page" means everywhere here: the URL without its #fragment or trailing slash."""
    return (url or "").split("#", 1)[0].rstrip("/")


def site_of(url):
    """The site a job is pinned to: the host without www. (the page itself when there is no host)."""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        host = ""
    return (host[4:] if host.startswith("www.") else host) or page_key(url)


async def page_eval(tid, expr, timeout=5):
    """Evaluate `expr` in tab `tid` over a short-lived page socket and return its value.

    One Runtime.evaluate, nothing else: no navigation, no focus, no input."""
    async with ws_connect(f"ws://127.0.0.1:{UPSTREAM}/devtools/page/{tid}", max_size=None) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                  "params": {"expression": expr, "returnByValue": True}}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if m.get("id") == 1:
                return m.get("result", {}).get("result", {}).get("value")


class HumanBusy(Exception):
    """An agent asked to reload, navigate, close or type into a tab the human is using."""

    def __init__(self, tid, seconds_ago, action):
        self.tid, self.seconds_ago, self.action = tid, seconds_ago, action
        super().__init__(
            f"the human is working in this tab right now (his last input was {seconds_ago:.0f}s ago), "
            f"so it was not {action}. Re-read the page (bb-peek) and continue from what it shows; "
            f"do not start over.")


class Humans:
    """Per tab: when did a person last type, click or paste in it?

    A tab counts as in use for HUMAN_QUIET seconds after his last input. While it
    is, no agent may reload it, navigate it, close it or send it input, through any
    path the broker controls."""

    def __init__(self):
        self.events = {}     # tid -> [(epoch seconds, kind)], oldest first
        self.agent = {}      # tid -> [epoch seconds an agent sent CDP input]
        self.touch = {}      # tid -> [epoch seconds of any agent command that can type (input or scripts)]
        self.masked = {}     # tid -> {(t, kind)} already judged the agent's own; never re-judged later
        self.tasks = {}      # tid -> the watcher task holding the listener in place

    # ---- what happened -------------------------------------------------------
    def agent_input(self, tid, t=None):
        t = time.time() if t is None else t
        a = self.agent.setdefault(tid, [])
        a.append(t)
        del a[:-2000]
        self.agent_touch(tid, t)

    def agent_touch(self, tid, t=None):
        """An agent ran a script, focused or typed in `tid`: its input/change echoes are masked."""
        a = self.touch.setdefault(tid, [])
        a.append(time.time() if t is None else t)
        a.sort()
        # 2026-10-25: a Gemini job polled for its answer ~200 times and the 200-entry cap dropped the
        # script that typed the question, so at release the page's own record of that typing was
        # re-judged as his and the tab was left open "for the human" (3 tabs piled up). Keep far more.
        del a[:-2000]

    def _is_echo(self, tid, t, kind=""):
        if any(a - 0.25 <= t <= a + AGENT_ECHO for a in self.agent.get(tid, ())):
            return True
        if kind not in ("input", "change"):
            return False             # his keys, clicks and pastes always count
        last = max((a for a in self.touch.get(tid, ()) if a - 0.25 <= t), default=None)
        if last is None or t - last > AGENT_ACTIVE:
            return False
        return not any(last < e <= t and k in GENUINE for e, k in self.events.get(tid, ()))

    def note(self, tid, t, kind):
        """Record one trusted input event. False when it was really the agent's own input."""
        m = self.masked.setdefault(tid, set())
        if (round(t, 3), kind) in m:
            return False
        if self._is_echo(tid, t, kind):
            m.add((round(t, 3), kind))
            if len(m) > 500:
                m.discard(min(m))
            return False
        ev = self.events.setdefault(tid, [])
        if (t, kind) in ev:
            return False
        ev.append((t, kind))
        ev.sort()
        del ev[:-100]
        return True

    def merge(self, tid, events):
        """Fold in events read back from the page's own window.__bbHuman record."""
        for e in events or ():
            try:
                self.note(tid, float(e[0]), str(e[1])[:20])
            except (TypeError, ValueError, IndexError):
                pass

    # ---- what that means -----------------------------------------------------
    def last(self, tid):
        ev = self.events.get(tid)
        return ev[-1][0] if ev else None

    def seconds_ago(self, tid, now=None):
        t = self.last(tid)
        return None if t is None else max(0.0, (now or time.time()) - t)

    def active(self, tid, now=None):
        ago = self.seconds_ago(tid, now)
        return ago is not None and ago < HUMAN_QUIET

    def touched_since(self, tid, t0):
        """Did he give this tab any real input at or after `t0`?"""
        return any(t >= t0 for t, _ in self.events.get(tid, ()))

    def since(self, tid):
        """Start of his current stretch of work in this tab (inputs less than HUMAN_QUIET apart)."""
        ev = self.events.get(tid)
        if not ev:
            return None
        start = ev[-1][0]
        for t, _ in reversed(ev[:-1]):
            if start - t >= HUMAN_QUIET:
                break
            start = t
        return start

    def info(self, tid, now=None):
        now = now or time.time()
        ago = self.seconds_ago(tid, now)
        return {"active": self.active(tid, now),
                "human_active_since": round(self.since(tid), 3) if self.active(tid, now) else None,
                "last_input_seconds_ago": None if ago is None else round(ago, 1),
                "inputs": len(self.events.get(tid, ())),
                "watched": tid in self.tasks}

    def guard(self, tid, action):
        """Raise HumanBusy if the human is using `tid`. `action` reads "reloaded", "closed"..."""
        if tid and self.active(tid):
            raise HumanBusy(tid, self.seconds_ago(tid), action)

    # ---- the listener --------------------------------------------------------
    def watch(self, tid):
        """Put the listener in tab `tid` and keep it there across navigations.

        A script added with Page.addScriptToEvaluateOnNewDocument lives only as
        long as the session that added it, so this holds one quiet page socket
        open per watched tab, for as long as the tab exists."""
        if tid and tid not in self.tasks:
            self.tasks[tid] = asyncio.ensure_future(self._watch(tid))

    def forget(self, tid):
        task = self.tasks.pop(tid, None)
        if task is not None:
            task.cancel()
        self.events.pop(tid, None)
        self.agent.pop(tid, None)
        self.touch.pop(tid, None)
        self.masked.pop(tid, None)

    async def _watch(self, tid):
        me = asyncio.current_task()
        try:
            async with ws_connect(f"ws://127.0.0.1:{UPSTREAM}/devtools/page/{tid}", max_size=None) as ws:
                setup = [("Runtime.addBinding", {"name": HUMAN_BINDING}),
                         ("Runtime.enable", {}),
                         ("Page.enable", {}),
                         ("Page.addScriptToEvaluateOnNewDocument", {"source": HUMAN_JS}),
                         ("Runtime.evaluate", {"expression": HUMAN_JS})]
                for i, (method, params) in enumerate(setup, 1):
                    await ws.send(json.dumps({"id": i, "method": method, "params": params}))
                async for raw in ws:
                    m = json.loads(raw)
                    p = m.get("params") or {}
                    if m.get("method") == "Runtime.bindingCalled" and p.get("name") == HUMAN_BINDING:
                        try:
                            t, kind = json.loads(p.get("payload") or "[]")
                            if self.note(tid, float(t), str(kind)[:20]):
                                log(f"human input in tab {tid[:12]} ({kind})")
                        except (TypeError, ValueError):
                            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            pass          # the tab closed, or the browser went away: nothing left to watch
        finally:
            if self.tasks.get(tid) is me:
                self.tasks.pop(tid, None)


HUMANS = Humans()


async def agent_gate(tid, method, params):
    """The refusal text when an agent may not send `method` to tab `tid` right now, else None.

    Rule 3: while the human is working in a tab, agents keep their hands off it.
    Rule 4: a tab that is on screen for a job is never reloaded or reopened mid-job.
    Called for every command an agent sends through the proxy to a tab."""
    if not method:
        return None
    # DOM.setFileInputFiles fires trusted input/change events on the file field, so it
    # counts as the agent's own input too (2026-10-23: a LinkedIn image upload in a
    # hidden tab read as "the human is working here" and blocked the agent's next click).
    if method.startswith("Input.") or method == "DOM.setFileInputFiles":
        if HUMANS.active(tid):
            return str(HumanBusy(tid, HUMANS.seconds_ago(tid), "typed or clicked in"))
        HUMANS.agent_input(tid)
        return None
    if method in AGENT_TOUCH:
        if tid:
            HUMANS.agent_touch(tid)  # reads must keep working while he is active: record only
        return None
    if method in ("Page.reload", "Page.navigate", "Target.closeTarget"):
        verb = {"Page.reload": "reloaded", "Page.navigate": "navigated", "Target.closeTarget": "closed"}[method]
        if HUMANS.active(tid):
            return str(HumanBusy(tid, HUMANS.seconds_ago(tid), verb))
        owner = STAGE.job_of(tid) if STAGE is not None else None
        if owner is None:
            return None
        if method == "Page.navigate":
            try:
                cur = (await UP.send("Target.getTargetInfo", targetId=tid)).get("targetInfo", {}).get("url", "")
            except Exception:
                cur = ""
            if page_key(cur) != page_key((params or {}).get("url")):
                return None       # going somewhere new is work, not a reload
        return (f"this tab is on screen for the job \"{owner}\" and that job is not done, so it was not "
                f"{verb}. Read what it shows now and continue from there. If you are stuck, stop and ask "
                f"the human in one line. The job's tab closes with bb-close {owner}.")
    return None


# ---------------------------------------------------------------------------
# the stage: the human's screen, held by one agent at a time
# ---------------------------------------------------------------------------
class StageBusy(Exception):
    def __init__(self, holder, seconds_left, url):
        self.holder, self.seconds_left, self.url = holder, seconds_left, url
        super().__init__(f"stage held by {holder} for another {seconds_left}s")


class StopAndAsk(Exception):
    """Rule 5: an agent that keeps opening and closing the same page is stuck. Refuse it."""

    def __init__(self, owner, url, times, minutes):
        self.owner, self.url = owner, url
        super().__init__(
            f"stop: \"{owner}\" has opened and closed {url} {times} times in the last {minutes} minutes. "
            f"It was not opened again. Do not retry. Ask the human in one line what is wrong "
            f"(bb-peek {owner} shows what the page says right now).")


PEEK_JS = r"""(() => {
  const n = %d;
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const label = (el) => clean(el.getAttribute('aria-label')
    || (el.labels && el.labels.length ? el.labels[0].innerText : '')
    || el.placeholder || el.name || el.id || el.type).slice(0, 60);
  const secret = (el, name) => el.type === 'password'
    || /^cc-|one-time-code|password/.test(el.autocomplete || '')
    || /card|cvv|cvc|ssn|social security|security code|passcode|\bpin\b/i.test(name);
  const fields = [];
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const t = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'file'].includes(t)) continue;
    const v = (t === 'checkbox' || t === 'radio') ? (el.checked ? 'checked' : '') : el.value;
    if (!v) continue;
    const name = label(el);
    fields.push({field: name, value: secret(el, name) ? '(hidden)' : String(v).slice(0, 80)});
    if (fields.length >= 30) break;
  }
  const h = window.__bbHuman;
  return JSON.stringify({url: location.href, title: document.title,
    text: clean(document.body && document.body.innerText).slice(0, n), fields: fields,
    human: h && Array.isArray(h.events) ? h.events.slice(-50) : []});
})()"""


class Stage:
    """Everything else in this file exists to keep agents OUT of the human's way.

    This is the opposite: the one path that deliberately interrupts him, for the
    times an agent genuinely has something to show. Because it is an interruption,
    it is the one thing that has to take turns.

    Two Claude Code sessions ran `open -a "Brave Browser" <url>` four minutes
    apart on 2026-09-20 and threw Matt's window back and forth between them. The
    proxy never saw it: `open -a` and AppleScript `activate` are macOS calls, not
    CDP, so swallowing Target.activateTarget did nothing. The fix is not to try to
    intercept them, which is not possible from here. It is to make a queued path
    that is easier to use than the rude one, and to make the rude one loud.

    Holding the stage does not block the caller: show() returns as soon as the
    page is up, and the hold decays on its own. A second agent that arrives while
    the page is fresh waits its turn instead of yanking the window mid-sentence.

    Jobs (2026-10-23). The owner name an agent passes is its job. A job owns
    exactly one tab, pinned to one site, from its first show until bb-close:
      * showing the same site again returns that same tab, as it is now;
      * it is never reloaded unless the caller asks, and never while the human
        is working in it;
      * opening and closing the same page over and over is refused (rule 5).
    """

    def __init__(self, up):
        self.up = up
        self._lock = asyncio.Lock()
        self._serial = 0
        self.holder = None
        self.url = None
        self.purpose = None
        self.until = 0
        self.tabs = {}           # owner -> that owner's job tab; nobody closes another's
        self.jobs = {}           # owner -> {"site", "asked", "keys", "since"}
        self.created = set()     # job tabs the broker itself opened; only these may be closed
        self.churn = {}          # (owner, page key) -> times that job was closed or reloaded

    def state(self):
        left = round(max(0.0, self.until - time.time()), 1)
        return {"held_by": self.holder if left else None, "showing": self.url if left else None,
                "purpose": self.purpose if left else None, "seconds_left": left,
                "tab": (self.tabs.get(self.holder) or "")[:12] or None,
                "tabs": {o: t[:12] for o, t in self.tabs.items()},
                "jobs": {o: {"site": j["site"], "tab": self.tabs.get(o, "")[:12],
                             "open_for": round(time.time() - j["since"]),
                             "human": HUMANS.info(self.tabs.get(o, "")),
                             "children": REG.child_list(self.tabs.get(o, "")) if REG is not None else []}
                         for o, j in self.jobs.items()}}

    def job_of(self, tid):
        """The owner whose open job this tab is, or None."""
        for o, t in self.tabs.items():
            if t == tid:
                return o
        return None

    def job_tab(self, owner, url):
        """The tab of `owner`'s open job when that job is on the same site as `url`."""
        job = self.jobs.get(owner)
        if job and self.tabs.get(owner) and job["site"] == site_of(url):
            return self.tabs[owner]
        return None

    # ---- rule 5: churn -------------------------------------------------------
    def _churn_count(self, owner, key):
        now = time.time()
        hits = [t for t in self.churn.get((owner, key), ()) if now - t < CHURN_WINDOW]
        if hits:
            self.churn[(owner, key)] = hits
        else:
            self.churn.pop((owner, key), None)
        return len(hits)

    def _refuse_churn(self, owner, url):
        n = self._churn_count(owner, page_key(url))
        if n >= CHURN_LIMIT:
            log(f"stage: refused {owner} reopening {url}: {n} open/close cycles in {CHURN_WINDOW}s")
            raise StopAndAsk(owner, page_key(url), n, max(1, CHURN_WINDOW // 60))

    def _note_churn(self, owner, keys):
        for k in keys:
            self.churn.setdefault((owner, k), []).append(time.time())

    async def _front(self, tid):
        """Bring the page, and on macOS the app, to the front. Exactly once.

        This is the only place in the project allowed to do this, which is the
        whole point: one door, with a queue in front of it.
        """
        try:
            await self.up.send("Target.activateTarget", targetId=tid)
        except Exception as e:
            log(f"stage: activateTarget failed ({e})")
        if sys.platform == "darwin" and STAGE_ACTIVATE_APP:
            try:
                pr = await asyncio.create_subprocess_exec(
                    "osascript", "-e", f'tell application "{BROWSER_APP}" to activate',
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                await asyncio.wait_for(pr.wait(), 5)
            except Exception as e:
                log(f"stage: activate failed ({e})")

    async def show(self, owner, url, purpose=None, wait=None, dwell=None, reload=False):
        wait = STAGE_WAIT if wait is None else int(wait)
        dwell = STAGE_DWELL if dwell is None else int(dwell)
        try:
            if wait <= 0:
                # wait_for(..., 0) times out even on a free lock, because the
                # acquire never gets to run. "Don't queue" checks the lock itself.
                if self._lock.locked():
                    raise asyncio.TimeoutError
                await self._lock.acquire()
            else:
                await asyncio.wait_for(self._lock.acquire(), timeout=wait)
        except asyncio.TimeoutError:
            raise StageBusy(self.holder, round(max(0.0, self.until - time.time()), 1), self.url)

        self._serial += 1
        mine = self._serial
        try:
            return await self._show_locked(mine, owner, url, purpose, dwell, reload)
        except BaseException:
            # A bad URL, Brave being down, a refusal, or the caller giving up must
            # not leave the screen locked: every later /show would get 409.
            self.free(mine)
            raise

    async def _live(self):
        """targetId -> targetInfo for every open page."""
        try:
            infos = (await self.up.send("Target.getTargets")).get("targetInfos", [])
        except Exception:
            return {}
        return {t["targetId"]: t for t in infos if t.get("type") == "page"}

    def _tab_showing(self, url, live, owner):
        """A tab to reuse instead of opening a copy, or None.

        In order: a job tab already on this page; this owner's own hidden (leased)
        tab on this page or this site, so a job never hops between a hidden and a
        visible copy; then any tab on this page, the human's own included."""
        want, site = page_key(url), site_of(url)
        shown = set(self.tabs.values())
        leased = [t for t, i in (REG.tabs.items() if REG is not None else ()) if i["owner"] == owner]
        same = [t for t, i in live.items() if page_key(i.get("url", "")) == want]
        for pool in ([t for t in same if t in shown], [t for t in same if t in leased],
                     [t for t in leased if t in live and site_of(live[t].get("url", "")) == site], same):
            if pool:
                return pool[0]
        return None

    async def _navigate(self, tid, url, reload=False):
        """Move tab `tid` to `url` in place; with reload=True, a fresh copy (cache bypassed)."""
        async with ws_connect(f"ws://127.0.0.1:{UPSTREAM}/devtools/page/{tid}", max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
            await asyncio.wait_for(ws.recv(), 10)
            if reload:
                await ws.send(json.dumps({"id": 2, "method": "Page.reload", "params": {"ignoreCache": True}}))
                await asyncio.wait_for(ws.recv(), 10)

    async def peek_tab(self, tid, chars=400):
        """What tab `tid` shows right now: final URL, title, the start of its visible
        text, the form fields that hold something (secrets masked), and whether
        the human is working in it.

        Read only. It evaluates one expression; it never navigates, reloads,
        focuses or types. Matt's rule, 2026-10-23: look at a page before touching
        it, because he may already have moved it forward (filled the form, solved
        the captcha, hit send).
        """
        chars = max(0, min(int(chars), 5000))
        info = {}
        try:
            info = (await self.up.send("Target.getTargetInfo", targetId=tid)).get("targetInfo", {})
        except Exception:
            pass
        out = {"target": tid[:12], "target_id": tid, "url": info.get("url", ""), "title": info.get("title", ""),
               "text": "", "fields": []}
        try:
            v = json.loads(await page_eval(tid, PEEK_JS % chars) or "{}")
            if isinstance(v, dict):
                out.update({k: v[k] for k in ("url", "title", "text") if isinstance(v.get(k), str)})
                if isinstance(v.get("fields"), list):
                    out["fields"] = v["fields"][:30]
                HUMANS.merge(tid, v.get("human"))
        except Exception as e:
            out["note"] = f"could not read the page ({type(e).__name__})"
        out["human"] = HUMANS.info(tid)
        out["human_active_since"] = out["human"]["human_active_since"]
        return out

    async def peek(self, match, chars=400):
        """Every open page tab whose job owner, lease owner or target id is `match`,
        or whose URL contains it. Includes tabs the human opened himself. Changes nothing."""
        if not match:
            return []
        live = await self._live()
        owners = {}
        for o, t in self.tabs.items():
            owners.setdefault(t, []).append(o)
        lessees = {t: i["owner"] for t, i in (REG.tabs.items() if REG is not None else ())}
        kids = dict(REG.children) if REG is not None else {}
        tids = [t for t, i in live.items()
                if match in owners.get(t, []) or match == lessees.get(t) or match in i.get("url", "")
                or (t in kids and kids[t]["owner"] == match)
                or (len(match) >= 8 and t.startswith(match))]
        hits = await asyncio.gather(*(self.peek_tab(tid, chars) for tid in tids))
        for tid, p in zip(tids, hits):
            p["shown_by"] = owners.get(tid) or None
            p["leased_by"] = lessees.get(tid)
            p["opened_by_broker"] = tid in self.created or tid in lessees or tid in kids
            if tid in kids:
                p["child_of"] = kids[tid]["parent"][:12]
                p["child_owner"] = kids[tid]["owner"]
            p["children"] = REG.child_list(tid) if REG is not None else []
        return list(hits)

    async def _show_locked(self, mine, owner, url, purpose, dwell, reload=False):
        # History of this method, because each line of it is a lesson:
        # 2026-09-16: re-showing a page closes the old tab first, so there is never
        #   a row of half-stale copies of the same page to pick from.
        # 2026-10-02: only ever THIS OWNER's tab. One shared stage tab let each
        #   agent's show close the page another agent had just put up.
        # 2026-10-22: "refresh the page that is already open, never open new tabs
        #   of the same thing." Reuse any tab already on the page.
        # 2026-10-23: "I filled out TruePeopleSearch and hit send ... you didn't
        #   recognize that on the screen and you're doing it again." Reloading that
        #   tab wiped his form, his captcha and his success page. Now a job owns one
        #   tab; showing it again brings it forward AS IS and hands back what it
        #   shows. It is reloaded only on request, and never while he works in it.
        key, site = page_key(url), site_of(url)
        live = await self._live()
        tid, job = self.tabs.get(owner), self.jobs.get(owner)
        if tid and tid not in live:                 # he closed it himself: that job is over
            self.tabs.pop(owner, None)
            self.jobs.pop(owner, None)
            self.created.discard(tid)
            tid = job = None
        if tid and job and job["site"] != site:
            # The owner moved on to another site, so the old job is done.
            await self._end_job(owner, live)
            tid = job = None

        if tid:                                      # rule 1: this job already has its tab
            if reload:
                HUMANS.guard(tid, "reloaded")
                self._refuse_churn(owner, url)
                how = "reloaded"
            elif job and key != job["asked"]:
                HUMANS.guard(tid, "moved to another page")
                how = "moved"
            else:
                how = "as is"
        else:
            self._refuse_churn(owner, url)
            tid = self._tab_showing(url, live, owner)
            if tid and reload:
                HUMANS.guard(tid, "reloaded")
            how = ("reloaded" if reload else "as is") if tid else "new"

        self.holder, self.url, self.purpose = owner, url, purpose
        self.until = time.time() + dwell
        if how == "new":
            res = await self.up.send("Target.createTarget", url=url, background=False)
            tid = res["targetId"]
            self.created.add(tid)
        elif how in ("reloaded", "moved"):
            await self._navigate(tid, url, reload=(how == "reloaded"))
            if how == "reloaded":
                self._note_churn(owner, [key])
        if REG is not None and tid in REG.tabs:
            self.created.add(tid)                    # the broker made it, as a hidden lease
        for o, t in list(self.tabs.items()):
            if t == tid and o != owner:              # one job per tab
                self.tabs.pop(o, None)
                self.jobs.pop(o, None)
        if self.tabs.get(owner) != tid or owner not in self.jobs:
            self.jobs[owner] = {"site": site, "asked": key, "keys": set(), "since": time.time()}
        self.tabs[owner] = tid
        self.jobs[owner]["asked"] = key
        self.jobs[owner]["keys"].add(key)
        HUMANS.watch(tid)
        await self._front(tid)
        words = {"new": "new tab", "as is": "already open, brought forward as is",
                 "reloaded": "reloaded in place (asked for)", "moved": "same job tab, moved to this page"}
        log(f"stage -> {owner}: {url} ({purpose or 'no purpose given'}), {words[how]}, held {dwell}s")
        asyncio.create_task(self._decay(mine, dwell))
        r = {"shown": url, "target_id": tid, "held_for": dwell, "owner": owner,
             "already_open": how != "new", "reloaded": how == "reloaded", "moved": how == "moved"}
        if how == "as is":
            r["page"] = await self.peek_tab(tid, 200)
        return r

    async def _end_job(self, owner, live=None):
        """Finish `owner`'s job: close its tab if the broker opened it, and the child
        tabs that tab opened. Returns ("closed", info, kids_closed, kids_left) or
        ("left", info, [], kids_left). A tab the human is working in is never
        closed: it is handed to him with its children, and the broker forgets it."""
        tid = self.tabs.get(owner)
        if tid and REG is not None:
            await REG.scan_children()          # while it is still the job's, so its children resolve
        tid = self.tabs.pop(owner, None)
        job = self.jobs.pop(owner, None)
        if not tid:
            return None
        if job:
            self._note_churn(owner, job["keys"])
        url = (live or {}).get(tid, {}).get("url", "")
        if tid not in self.created:
            # His own tab, shown for this job: it stays, and so does whatever he opened from it.
            kids = REG.leave_children(tid, "opened from the human's own tab") if REG is not None else []
            return ("left", {"owner": owner, "target": tid[:12], "why": "the human's own tab"}, [], kids)
        self.created.discard(tid)
        if REG is not None and REG.tabs.pop(tid, None) is not None:
            REG._save()
        if HUMANS.active(tid):
            kids = REG.leave_children(tid, "its tab is the human's now") if REG is not None else []
            HUMANS.forget(tid)
            log(f"stage tab {tid[:12]} left open for the human ({owner}): he is working in it")
            return ("left", {"owner": owner, "target": tid[:12],
                             "why": "the human is working in it, so it is his now"}, [], kids)
        kids_closed, kids_left = (await REG.close_children(tid, "job done")) if REG is not None else ([], [])
        HUMANS.forget(tid)
        try:
            await self.up.send("Target.closeTarget", targetId=tid)
        except Exception:
            pass
        log(f"stage tab {tid[:12]} closed ({owner}, job done)")
        return ("closed", {"owner": owner, "target": tid[:12], "url": url}, kids_closed, kids_left)

    async def unshow(self, match):
        """Finish the job(s) whose owner is `match`, or whose page URL contains it.

        Matt's rule, 2026-10-23: "quit leaving tabs open after you do things." An
        agent that put a page up closes it when the task is done (bb-close). Only
        tabs the broker itself opened are closed. A tab the human already had open,
        or is working in right now, is left to him.
        """
        if not match:
            return {"closed": [], "left_open": []}
        live = await self._live()
        closed, left = [], []
        for o, tid in list(self.tabs.items()):
            if o != match and match not in live.get(tid, {}).get("url", ""):
                continue
            res = await self._end_job(o, live)
            if res:
                (closed if res[0] == "closed" else left).append(res[1])
                closed += res[2]
                left += res[3]
            if o == self.holder:
                self.free(self._serial)
        if REG is not None:
            # Children of this owner whose parent is already gone (the human closed
            # it, or a lease ended on it): bb-close <owner> still cleans them up.
            for c, i in list(REG.children.items()):
                if i["owner"] == match and i["parent"] not in live:
                    kc, kl = await REG.close_child(c, "job done")
                    closed += kc
                    left += kl
        return {"closed": closed, "left_open": left}

    async def _decay(self, serial, dwell):
        await asyncio.sleep(dwell)
        self.free(serial)

    def free(self, serial=None):
        if serial is not None and serial != self._serial:
            return False                      # someone else's turn by now
        if self._lock.locked():
            self.holder = None
            self.url = None
            self.purpose = None
            self.until = 0
            self._lock.release()
            return True
        return False



async def close_for_matt(prefix, asked):
    """Close the tab whose id starts with `prefix` because Matt asked in words. Refused while he is using it."""
    import urllib.request as _u
    try:
        tabs = json.loads(_u.urlopen(f"http://127.0.0.1:{UPSTREAM}/json/list", timeout=5).read())
    except Exception as e:
        return {"closed": [], "error": f"browser not answering: {e}"}
    hit = [t for t in tabs if t.get("type") == "page" and t.get("id", "").startswith(prefix)]
    if len(hit) != 1:
        return {"closed": [], "error": f"{len(hit)} tabs match {prefix}; need exactly one"}
    tid = hit[0]["id"]
    if HUMANS.active(tid):
        return {"closed": [], "left_open": [{"target": tid[:12], "why": "he is typing or clicking in it right now"}]}
    try:
        _u.urlopen(f"http://127.0.0.1:{UPSTREAM}/json/close/{tid}", timeout=5).read()
    except Exception as e:
        return {"closed": [], "error": f"close failed: {e}"}
    if REG is not None:
        REG.tabs.pop(tid, None)
    HUMANS.forget(tid)
    log(f"tab {tid[:12]} closed because Matt asked: \"{asked[:120]}\" ({hit[0].get('url', '')[:80]})")
    return {"closed": [{"target": tid[:12], "url": hit[0].get("url", "")}]}

REG: Registry = None
UP: Upstream = None
STAGE: Stage = None


# ---------------------------------------------------------------------------
# the CDP-compatible front door on LISTEN
# ---------------------------------------------------------------------------
def _resp(status, reason, body=b"", ctype="application/json"):
    return Response(status, reason, Headers([("Content-Type", ctype),
                                             ("Content-Length", str(len(body))),
                                             ("Connection", "close")]), body)


async def _pool_entries():
    """What a legacy client sees on GET /json: one unclaimed tab we made, reserved
    for this caller for a few seconds so a second caller can't be handed the same
    one before the first attaches. Made fresh if none is free."""
    free = REG.free_pool()
    tid = free[0] if free else await REG.create("pool", "about:blank", hidden=True, kind="pool")
    REG.tabs[tid]["listed_until"] = time.time() + POOL_HOLD
    e = REG.target_info(tid)
    try:
        for t in (await UP.send("Target.getTargets")).get("targetInfos", []):
            if t["targetId"] == tid:
                e["title"], e["url"] = t.get("title", ""), t.get("url", "")
    except Exception:
        pass
    return [e]


async def route_http(method, path):
    """HTTP endpoints of the DevTools remote-debugging surface.
    Returns (status line, content type, body bytes)."""
    u = urlparse(path)
    # Playwright asks for /json/version/ with a trailing slash; Chrome tolerates it.
    u = u._replace(path=(u.path.rstrip("/") or "/"))
    j = lambda obj: ("200 OK", "application/json", json.dumps(obj, indent=2).encode())
    if u.path == "/json/version":
        v = dict(UP.version or {})
        v["Browser"] = (v.get("Browser", "") + " (browser-broker)").strip()
        v["webSocketDebuggerUrl"] = f"ws://127.0.0.1:{LISTEN}/devtools/browser/{uuid.uuid4().hex}"
        return j(v)
    if u.path in ("/json", "/json/list"):
        return j(await _pool_entries())
    if u.path == "/json/new":       # Chrome 111+ wants PUT; GET still accepted here
        url = unquote(u.query) if u.query else "about:blank"
        tid = await REG.create("pool", url, hidden=True, kind="pool")
        REG.tabs[tid]["listed_until"] = time.time() + POOL_HOLD
        return j(REG.target_info(tid))
    if u.path.startswith("/json/activate/"):
        return ("200 OK", "text/plain", b"Target activated (noop)")
    if u.path.startswith("/json/close/"):
        tid = u.path.rsplit("/", 1)[-1]
        # only unleased pool tabs: a REST lease or a browser client's tab is not
        # some other caller's to close just because it knows the id
        if REG.tabs.get(tid, {}).get("kind") == "pool":
            await REG.close(tid, "client closed it")
            return ("200 OK", "text/plain", b"Target is closing")
        return ("404 Not Found", "text/plain", b"No such target id")
    if u.path == "/json/protocol":
        return j(await UP._http("/json/protocol"))
    return ("404 Not Found", "text/plain", b"Not Found")


async def process_request(connection, request):
    """The inner websocket server only ever sees upgrades (the front door routes
    plain HTTP itself); anything else is refused."""
    if request.path.startswith("/devtools/"):
        return None
    return _resp(404, "Not Found")


INNER_PORT = None      # where the websocket server really listens (ephemeral, loopback)


async def _splice(r, w):
    try:
        while True:
            data = await r.read(65536)
            if not data:
                break
            w.write(data)
            await w.drain()
    except Exception:
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def front_door(reader, writer):
    """Port 9222. The websockets library refuses any HTTP method but GET before
    our code runs, so PUT /json/new (what Chrome 111+ clients send) failed. Plain
    HTTP is answered here; websocket upgrades are piped byte-for-byte to the
    inner websocket server. A bare port probe just gets closed, quietly."""
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    except Exception:
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) < 2:
        writer.close()
        return
    method, path = parts[0].upper(), parts[1]
    headers = {}
    for l in lines[1:]:
        if ":" in l:
            k, v = l.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    if "websocket" in headers.get("upgrade", "").lower():
        try:
            ur, uw = await asyncio.open_connection("127.0.0.1", INNER_PORT)
        except Exception:
            writer.close()
            return
        uw.write(head)
        await asyncio.gather(_splice(reader, uw), _splice(ur, writer))
        return
    try:
        status, ctype, body = await route_http(method, path)
    except Exception as e:
        status, ctype, body = "500 Internal Server Error", "text/plain", f"{type(e).__name__}: {e}".encode()
    try:
        writer.write((f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
                      f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def _pump(src, dst, transform=None):
    """Copy messages src -> dst, optionally rewriting/dropping via transform."""
    try:
        async for raw in src:
            if transform:
                out = await transform(raw)
                if out is None:
                    continue
                raw = out
            await dst.send(raw)
    except websockets.ConnectionClosed:
        pass
    finally:
        try:
            await dst.close()
        except Exception:
            pass


async def handle_ws(connection):
    path = connection.request.path
    if path.startswith("/devtools/page/"):
        await _page_session(connection, path.rsplit("/", 1)[-1])
    elif path.startswith("/devtools/browser/"):
        await _browser_session(connection)
    else:
        await connection.close(1008, "unknown endpoint")


async def _page_session(client, tid):
    info = REG.tabs.get(tid)
    if info is None:
        await client.close(1008, "not your tab")
        return
    if info["kind"] == "pool" and info["attached"] > 0:
        await client.close(1008, "tab already in use")   # a pool tab is one client's
        return
    info["attached"] += 1
    owner = info["owner"]
    try:
        up = await ws_connect(f"ws://127.0.0.1:{UPSTREAM}/devtools/page/{tid}", max_size=None)
    except Exception as e:
        info["attached"] -= 1
        await client.close(1011, f"browser unreachable: {e}")
        return

    async def c2u(raw):
        try:
            m = json.loads(raw)
        except Exception:
            return raw
        if m.get("method") in FOCUS_STEALERS or m.get("method") in BROWSER_KILLERS:
            out = {"id": m.get("id"), "result": {}}
            if "sessionId" in m:
                out["sessionId"] = m["sessionId"]
            await client.send(json.dumps(out))
            return None
        no = await agent_gate(tid, m.get("method"), m.get("params"))
        if no:
            out = {"id": m.get("id"), "error": {"code": -32000, "message": "browser-broker: " + no}}
            if "sessionId" in m:
                out["sessionId"] = m["sessionId"]
            await client.send(json.dumps(out))
            return None
        return raw

    try:
        await asyncio.gather(_pump(client, up, c2u), _pump(up, client))
    finally:
        info = REG.tabs.get(tid)
        if info is not None:
            info["attached"] -= 1
            if info["kind"] == "pool" and info["attached"] <= 0:
                await REG.close(tid, "page socket closed")
            elif info["kind"] == "rest":
                pass    # REST lease outlives the socket; the reaper handles TTL
        log(f"page socket closed for {tid[:12]} ({owner})")


async def _browser_session(client):
    """One browser-level client == one owner. It sees only what it created."""
    owner = "ws:" + uuid.uuid4().hex[:8]
    try:
        up = await ws_connect(UP.version["webSocketDebuggerUrl"], max_size=None)
    except Exception as e:
        await client.close(1011, f"browser unreachable: {e}")
        return
    sessions = {}        # sessionId -> targetId (flattened attach)
    seen = set()         # every target this client was ever shown, so it hears when they close
    contexts = set()     # browser contexts this client created; disposed when it leaves
    log(f"browser client {owner} connected")

    def mine(tid):
        return tid in REG.tabs and REG.tabs[tid]["owner"] == owner

    async def settled_mine(tid):
        """A target Chrome just announced may be ours but not yet registered
        (createTarget reply still in flight on the control socket). Wait for it
        ONLY while this client has a create in flight — otherwise decide now, so
        a foreign tab never stalls the event pump."""
        if tid in REG.tabs:
            return mine(tid)
        if not creating["n"]:
            return False
        for _ in range(40):
            await asyncio.sleep(0.025)
            if tid in seen:
                return True     # ours, even if the client has already closed it
            if tid in REG.tabs:
                return mine(tid)
        return False

    async def make(url, context=None):
        creating["n"] += 1
        try:
            tid = await REG.create(owner, url or "about:blank", hidden=True, kind="ws", context=context)
        finally:
            creating["n"] -= 1
        seen.add(tid)
        return tid

    async def reply(m, **body):
        out = {"id": m.get("id"), **body}
        if "sessionId" in m:
            out["sessionId"] = m["sessionId"]
        await client.send(json.dumps(out))

    async def c2u(raw):
        try:
            m = json.loads(raw)
        except Exception:
            return raw
        try:
            return await _c2u(m, raw)
        except websockets.ConnectionClosed:
            raise
        except Exception as e:
            # A slow or failed upstream call answers THIS command with an error.
            # It used to escape the pump and end the session, closing every tab
            # the client had.
            if m.get("id") is not None:
                await reply(m, error={"code": -32000, "message": f"browser-broker: {e}"})
            return None

    async def _c2u(m, raw):
        method, mid, p = m.get("method"), m.get("id"), m.get("params") or {}
        if method in FOCUS_STEALERS:
            await reply(m, result={})
            return None
        if method in BROWSER_KILLERS:
            log(f"{owner} sent {method} -- swallowed, the browser is the human's")
            await reply(m, result={})
            if method == "Browser.close":
                # what the client wanted is to be done; let it go so its close() resolves
                asyncio.ensure_future(client.close())
            return None
        if method == "Target.attachToBrowserTarget":
            await reply(m, error={"code": -32000, "message": "browser-broker: browser target is not shared"})
            return None
        if method == "Target.createBrowserContext":
            res = await UP.send(method, **p)
            contexts.add(res.get("browserContextId"))
            await reply(m, result=res)
            return None
        if method == "Target.disposeBrowserContext":
            if p.get("browserContextId") not in contexts:
                await reply(m, error={"code": -32000, "message": "Failed to find context with id"})
                return None
            contexts.discard(p.get("browserContextId"))
            for tid, i in list(REG.tabs.items()):
                if i["owner"] == owner and i.get("context") == p.get("browserContextId"):
                    REG.tabs.pop(tid, None)
            REG._save()
            await reply(m, result=await UP.send(method, **p))
            return None
        if method == "Target.setAutoAttach":
            auto_attach["on"] = bool(p.get("autoAttach"))
        if method == "Target.createTarget":
            ctx = p.get("browserContextId")
            if ctx and ctx not in contexts:
                await reply(m, error={"code": -32000, "message": "Failed to find browser context with id " + str(ctx)})
                return None
            tid = await make(p.get("url"), ctx)
            if ctx:
                REG.tabs[tid]["context"] = ctx
            if auto_attach["on"]:
                # Playwright/Puppeteer expect attachedToTarget for the new page to
                # arrive BEFORE the createTarget reply. Chrome guarantees that on
                # one socket; we are two sockets, so hold the reply until the
                # attach event has gone through.
                for _ in range(80):
                    if tid in sessions.values():
                        break
                    await asyncio.sleep(0.025)
            await reply(m, result={"targetId": tid})
            return None
        if method == "Target.getTargets":
            infos = (await UP.send("Target.getTargets")).get("targetInfos", [])
            infos = [t for t in infos if mine(t["targetId"])]
            if not infos:
                # nothing yet -> give this client one so "first page" works
                tid = await make("about:blank")
                infos = [t for t in (await UP.send("Target.getTargets")).get("targetInfos", []) if t["targetId"] == tid]
            await reply(m, result={"targetInfos": infos})
            return None
        if method == "Target.setDiscoverTargets":
            # Forwarded, so the client gets Chrome's real targetCreated /
            # targetInfoChanged / targetDestroyed stream; u2c drops everyone else's.
            discover["on"] = bool(p.get("discover"))
            return raw
        if method in ("Target.attachToTarget", "Target.closeTarget", "Target.detachFromTarget",
                      "Browser.getWindowForTarget"):
            tid = p.get("targetId")
            if method == "Target.detachFromTarget":
                tid = sessions.get(p.get("sessionId"), tid)
            if tid and not mine(tid):
                await reply(m, error={"code": -32000, "message": "No target with given id found"})
                return None
            if method == "Target.closeTarget":
                no = await agent_gate(tid, method, p)
                if no:
                    await reply(m, error={"code": -32000, "message": "browser-broker: " + no})
                    return None
                await REG.close(tid, "client closed it")
                await reply(m, result={"success": True})
                return None
        if method == "Target.setAutoAttach" and p.get("flatten") is not None:
            pass    # allowed; auto-attach events are filtered below
        if m.get("sessionId") in sessions:
            no = await agent_gate(sessions[m["sessionId"]], method, p)
            if no:
                await reply(m, error={"code": -32000, "message": "browser-broker: " + no})
                return None
        return raw

    async def u2c(raw):
        try:
            m = json.loads(raw)
        except Exception:
            return raw
        method, p, sid = m.get("method"), m.get("params") or {}, m.get("sessionId")
        if method == "Target.attachedToTarget":
            child, tid = p.get("sessionId"), (p.get("targetInfo") or {}).get("targetId")
            # nested under one of our sessions (OOPIF, worker) or one of our pages
            if sid in sessions or await settled_mine(tid):
                sessions[child] = tid
                seen.add(tid)
                return raw
            try:    # someone else's tab auto-attached to us: let go quietly
                await up.send(json.dumps({"id": 0, "method": "Target.detachFromTarget",
                                          "params": {"sessionId": child}}))
            except Exception:
                pass
            return None
        if method == "Target.detachedFromTarget":
            known = p.get("sessionId") in sessions
            sessions.pop(p.get("sessionId"), None)
            return raw if known else None
        if sid is not None:
            return raw if sid in sessions else None       # session traffic: ours or nothing
        if method in ("Target.targetCreated", "Target.targetInfoChanged", "Target.targetDestroyed"):
            tid = p.get("targetId") or (p.get("targetInfo") or {}).get("targetId")
            # REG forgets a tab the moment it is closed, so its targetDestroyed
            # would be dropped without `seen` and the client never hears it went.
            if tid in seen or tid in sessions.values() or await settled_mine(tid):
                seen.add(tid)
                if method == "Target.targetDestroyed":
                    seen.discard(tid)
                return raw
            return None
        return raw

    discover = {"on": False}
    auto_attach = {"on": False}
    creating = {"n": 0}
    try:
        await asyncio.gather(_pump(client, up, c2u), _pump(up, client, u2c))
    finally:
        await REG.release_owner(owner)
        for ctx in contexts:
            try:
                await UP.send("Target.disposeBrowserContext", browserContextId=ctx)
            except Exception:
                pass
        log(f"browser client {owner} disconnected")


# ---------------------------------------------------------------------------
# REST lease API on API port (kept compatible with broker_client.py)
# ---------------------------------------------------------------------------
class ApiHandler(BaseHTTPRequestHandler):
    loop = None

    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _run(self, coro, timeout=30):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout)
        except concurrent.futures.TimeoutError:
            # Without this the coroutine kept running: a /show whose caller had
            # given up still put the page up later, on top of bb-show's fallback.
            fut.cancel()
            raise

    def do_GET(self):
        if self.path.startswith("/status"):
            s = REG.status()
            s["brave_alive"] = self._run(UP.alive())
            s["leases"] = [{"lease_id": t["lease_id"], "owner": t["owner"], "purpose": None,
                            "target": t["target"], "expires_in": t["expires_in"],
                            "children": t["children"]}
                           for t in s["tabs"] if t["kind"] == "rest"]
            return self._reply(200, s)
        if self.path.startswith("/health"):
            return self._reply(200, {"ok": True, "brave": self._run(UP.alive())})
        if self.path.startswith("/human"):
            # How long since the human last typed or clicked in ANY tab the broker watches.
            # bb-click asks this (with the OS idle time) before it may take a step visibly.
            last = max((HUMANS.last(t) or 0 for t in list(HUMANS.events)), default=0)
            ago = round(time.time() - last, 1) if last else None
            return self._reply(200, {"last_input_seconds_ago": ago, "tabs_watched": len(HUMANS.tasks)})
        if self.path.startswith("/stage"):
            return self._reply(200, STAGE.state())
        if self.path.startswith("/peek"):
            # Read only: what matching tabs show right now, the human's own included.
            from urllib.parse import urlsplit, parse_qs
            q = parse_qs(urlsplit(self.path).query)
            match = (q.get("match") or [""])[0]
            if not match:
                return self._reply(400, {"error": "peek needs ?match=<owner|url-substring>"})
            try:
                chars = int((q.get("chars") or ["400"])[0])
            except ValueError:
                chars = 400
            try:
                return self._reply(200, {"match": match, "tabs": self._run(STAGE.peek(match, chars))})
            except concurrent.futures.TimeoutError:
                return self._reply(504, {"error": "browser did not answer in time"})
        self._reply(404, {"error": "no such endpoint"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}")
            if self.path.startswith("/lease"):
                ttl = int(b.get("ttl", DEFAULT_TTL))
                owner = b.get("owner", "anonymous")
                # Rule 1: a job that is already on screen for this owner and site
                # is leased as it is. A hidden second copy is what agents used to
                # fill in while the human was looking at (and typing in) the first.
                shared = STAGE.job_tab(owner, b["url"]) if b.get("url") else None
                if shared and shared not in REG.tabs:
                    tid = self._run(REG.adopt(shared, owner, ttl))
                else:
                    shared = None
                    tid = self._run(REG.create(owner, b.get("url"), hidden=bool(b.get("hidden", True)),
                                               kind="rest", ttl=ttl))
                i = REG.tabs[tid]
                i["attached"] = 1          # a REST lease counts as held from grant
                i["purpose"] = b.get("purpose")
                return self._reply(200, {"lease_id": i["lease_id"], "target_id": tid,
                                         "ws_url": f"ws://127.0.0.1:{LISTEN}/devtools/page/{tid}",
                                         "expires_in": ttl, "shared_with_shown_job": bool(shared)})
            if self.path.startswith("/renew"):
                # Pool and browser-client tabs carry lease_id None: a body with no
                # lease_id must not match them.
                if not b.get("lease_id"):
                    return self._reply(400, {"error": "lease_id is required"})
                for tid, i in list(REG.tabs.items()):
                    if i.get("lease_id") == b.get("lease_id"):
                        i["expires"] = time.time() + int(b.get("ttl", DEFAULT_TTL))
                        return self._reply(200, {"lease_id": b["lease_id"], "expires_in": int(b.get("ttl", DEFAULT_TTL))})
                return self._reply(404, {"error": "unknown lease"})
            if self.path.startswith("/release"):
                if not b.get("lease_id"):
                    return self._reply(400, {"error": "lease_id is required"})
                for tid, i in list(REG.tabs.items()):
                    if i.get("lease_id") == b.get("lease_id"):
                        kids = {}
                        closed = self._run(REG.close(tid, "released", kids))
                        r = {"released": b["lease_id"], "tab_closed": bool(closed)}
                        r.update({k: v for k, v in kids.items() if v})     # only when it had child tabs
                        if not closed:
                            r["left_open"] = ("it is a job tab on screen; bb-close finishes the job"
                                              if STAGE.job_of(tid) else "the human is working in it")
                        return self._reply(200, r)
                return self._reply(404, {"error": "unknown lease"})
            if self.path.startswith("/show"):
                if not b.get("url"):
                    return self._reply(400, {"error": "show needs a url"})
                try:
                    wait = STAGE_WAIT if b.get("wait") is None else int(b["wait"])
                    return self._reply(200, self._run(STAGE.show(
                        b.get("owner", "anonymous"), b["url"], b.get("purpose"),
                        wait, b.get("dwell"), bool(b.get("reload"))), timeout=wait + 15))
                except StageBusy as e:
                    return self._reply(409, {"error": str(e), "held_by": e.holder,
                                             "seconds_left": e.seconds_left, "showing": e.url})
                except HumanBusy as e:
                    # Rule 3: hand back what he has done so far, so the agent picks up from there.
                    page = None
                    try:
                        page = self._run(STAGE.peek_tab(e.tid, 400), timeout=10)
                    except Exception:
                        pass
                    return self._reply(423, {"error": str(e), "human_active": True,
                                             "seconds_ago": round(e.seconds_ago, 1), "page": page})
                except StopAndAsk as e:
                    return self._reply(429, {"error": str(e), "stop_and_ask": True})
            if self.path.startswith("/unshow"):
                match = b.get("match") or b.get("owner") or b.get("url")
                if not match:
                    return self._reply(400, {"error": "unshow needs an owner or a url substring (match)"})
                return self._reply(200, self._run(STAGE.unshow(match)))
            if self.path.startswith("/close-for-matt"):
                # 2026-10-26 Matt: "close all the tabs on my M5 that you're finished with". A tab the broker counts as
                # his (it lost track of whose it was, or he really used it) can be closed only when he asked for it
                # in words (quoted in the log), by exact target id, and never while he is typing or clicking in it.
                tgt, asked = str(b.get("target") or ""), str(b.get("asked") or "").strip()
                if len(tgt) < 8 or not asked:
                    return self._reply(400, {"error": "need target (8+ chars of the tab id) and asked (Matt's words)"})
                return self._reply(200, self._run(close_for_matt(tgt, asked)))
            if self.path.startswith("/unstage"):
                return self._reply(200, {"freed": STAGE.free()})
            if self.path.startswith("/adopt"):
                return self._reply(410, {"error": "adopt is not supported by the proxy — agents only ever get tabs the broker made"})
            self._reply(404, {"error": "no such endpoint"})
        except concurrent.futures.TimeoutError:
            self._reply(504, {"error": "browser did not answer in time"})
        except Exception as e:
            self._reply(500, {"error": f"{type(e).__name__}: {e}"})


def _trim_stdio():
    """launchd / Task Scheduler point our stdout and stderr at plain files that
    nothing rotates. Empty them when they get big."""
    for fd in (1, 2):
        try:
            st = os.fstat(fd)
            if stat.S_ISREG(st.st_mode) and st.st_size > STDIO_MAX:
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            pass


async def background():
    last_hb = 0
    while True:
        try:
            _trim_stdio()
            await REG.reap()
            if time.time() - last_hb > HEARTBEAT_EVERY:
                s = REG.status()
                log(f"heartbeat — {s['count']} broker tabs, browser {'up' if await UP.alive() else 'DOWN'}")
                last_hb = time.time()
        except Exception as e:
            log(f"background error: {type(e).__name__}: {e}")
        await asyncio.sleep(5)


def _claim_ports():
    """Take LISTEN and API before anything else happens.

    2026-10-07: a second supervisor started a second proxy every 10 seconds.
    Each one connected upstream, ran close_leftovers() — which closed every tab
    the RUNNING proxy owned — and only then failed to bind and exited. Agents
    lost their tabs mid-run every 10 seconds and the cause was invisible, because
    the duplicate never lived long enough to be in the process list. A doomed
    instance must now die before it can touch a single tab."""
    import socket
    # 2026-10-23: after `launchctl kickstart -k` the new proxy decided a live proxy
    # owned its ports, exited 0, and launchd (restart only on failure) left the
    # broker down. Two causes, both handled: TIME_WAIT leftovers (below) and an
    # old process still letting go, which gets a few seconds of grace. A genuine
    # duplicate still exits without touching anything.
    deadline = time.time() + PORT_GRACE
    while True:
        held, busy = [], None
        for port in (LISTEN, API):
            s = socket.socket()
            # The real servers bind with SO_REUSEADDR, so the probe must too.
            # Without it, the old process's connections still in TIME_WAIT
            # look exactly like a live proxy for half a minute after a restart.
            # An actual listener on the port still makes this bind fail.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                s.close()
                busy = port
                break
            held.append(s)
        for h in held:          # hand the ports back to the real servers below
            h.close()
        if busy is None:
            return
        if time.time() >= deadline:
            log(f"another proxy already owns port {busy} — exiting without "
                f"touching any tabs")
            sys.exit(0)
        time.sleep(0.5)


async def main():
    global REG, UP, STAGE, INNER_PORT
    _claim_ports()
    UP = Upstream()
    try:
        await UP.connect()
    except Exception as e:
        log(f"no browser answering CDP on {UPSTREAM} ({e}) — launch it with "
            f"--remote-debugging-port={UPSTREAM} first (see launch-browser.sh)")
        sys.exit(1)
    REG = Registry(UP)
    STAGE = Stage(UP)
    loop = asyncio.get_running_loop()
    ApiHandler.loop = loop
    api = ThreadingHTTPServer(("127.0.0.1", API), ApiHandler)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    asyncio.create_task(background())

    async with ws_serve(handle_ws, "127.0.0.1", 0, process_request=process_request,
                        max_size=None, ping_interval=None) as inner:
        INNER_PORT = inner.sockets[0].getsockname()[1]
        front = await asyncio.start_server(front_door, "127.0.0.1", LISTEN, limit=1 << 20)
        async with front:
            # Only once we genuinely own the ports is it safe to clean up after
            # a previous run. Before the bind, "leftovers" may be another live
            # proxy's working tabs.
            await REG.close_leftovers()
            log(f"proxy up: CDP on {LISTEN} (upstream browser {UPSTREAM}), lease API on {API}")
            await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
