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

    async def _reader(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
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
        for tid in old & live:
            try:
                await self.up.send("Target.closeTarget", targetId=tid)
            except Exception:
                pass
        if old & live:
            log(f"closed {len(old & live)} tab(s) left over from the last run")
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
        log(f"tab {tid[:12]} -> {owner} ({kind}{', hidden' if hidden else ''})")
        return tid

    def owned_by(self, owner):
        return [t for t, i in self.tabs.items() if i["owner"] == owner]

    def free_pool(self):
        """Tabs made for legacy /json clients that nobody has attached to yet."""
        now = time.time()
        return [t for t, i in self.tabs.items()
                if i["kind"] == "pool" and i["attached"] == 0 and i["listed_until"] <= now]

    async def close(self, tid, why=""):
        info = self.tabs.pop(tid, None)
        if info is None:
            return
        self._save()
        try:
            await self.up.send("Target.closeTarget", targetId=tid)
        except Exception:
            pass
        log(f"tab {tid[:12]} closed ({info['owner']}{', ' + why if why else ''})")

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
        if gone:
            self._save()

    def status(self):
        now = time.time()
        return {"tabs": [{"target": t[:12], "owner": i["owner"], "kind": i["kind"],
                          "hidden": i["hidden"], "attached": i["attached"],
                          "expires_in": round(i["expires"] - now, 1) if i["kind"] != "ws" else None,
                          "lease_id": i["lease_id"]} for t, i in self.tabs.items()],
                "count": len(self.tabs)}

    def target_info(self, tid):
        """Shape a /json entry for a tab we own."""
        return {"id": tid, "type": "page", "title": "", "url": "",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{LISTEN}/devtools/page/{tid}",
                "devtoolsFrontendUrl": f"/devtools/inspector.html?ws=127.0.0.1:{LISTEN}/devtools/page/{tid}"}



# ---------------------------------------------------------------------------
# the stage: the human's screen, held by one agent at a time
# ---------------------------------------------------------------------------
class StageBusy(Exception):
    def __init__(self, holder, seconds_left, url):
        self.holder, self.seconds_left, self.url = holder, seconds_left, url
        super().__init__(f"stage held by {holder} for another {seconds_left}s")


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
    """

    def __init__(self, up):
        self.up = up
        self._lock = asyncio.Lock()
        self._serial = 0
        self.holder = None
        self.url = None
        self.purpose = None
        self.until = 0
        self.tabs = {}           # owner -> that owner's stage tab; nobody closes another's

    def state(self):
        left = round(max(0.0, self.until - time.time()), 1)
        return {"held_by": self.holder if left else None, "showing": self.url if left else None,
                "purpose": self.purpose if left else None, "seconds_left": left,
                "tab": (self.tabs.get(self.holder) or "")[:12] or None,
                "tabs": {o: t[:12] for o, t in self.tabs.items()}}

    async def _front(self, tid):
        """Bring the page, and on macOS the app, to the front. Exactly once.

        This is the only place in the project allowed to do this, which is the
        whole point: one door, with a queue in front of it.
        """
        try:
            await self.up.send("Target.activateTarget", targetId=tid)
        except Exception as e:
            log(f"stage: activateTarget failed ({e})")
        if sys.platform == "darwin":
            try:
                pr = await asyncio.create_subprocess_exec(
                    "osascript", "-e", f'tell application "{BROWSER_APP}" to activate',
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                await asyncio.wait_for(pr.wait(), 5)
            except Exception as e:
                log(f"stage: activate failed ({e})")

    async def show(self, owner, url, purpose=None, wait=None, dwell=None):
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
            return await self._show_locked(mine, owner, url, purpose, dwell)
        except BaseException:
            # A bad URL, Brave being down, or the caller giving up must not leave
            # the screen locked: every later /show would get 409 until /unstage.
            self.free(mine)
            raise

    async def _tab_showing(self, url):
        """Target id of an open page tab already on this URL (fragment ignored), or None."""
        want = url.split("#", 1)[0].rstrip("/")
        try:
            infos = (await self.up.send("Target.getTargets")).get("targetInfos", [])
        except Exception:
            return None
        mine = set(self.tabs.values())
        hits = [t for t in infos if t.get("type") == "page"
                and t.get("url", "").split("#", 1)[0].rstrip("/") == want]
        # Prefer a tab the stage already put up, then any other.
        hits.sort(key=lambda t: t["targetId"] not in mine)
        return hits[0]["targetId"] if hits else None

    async def _navigate(self, tid, url):
        """Reload tab `tid` in place at `url` (fresh copy, cache bypassed). True on success."""
        try:
            async with ws_connect(f"ws://127.0.0.1:{UPSTREAM}/devtools/page/{tid}", max_size=None) as ws:
                await ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
                await asyncio.wait_for(ws.recv(), 10)
                await ws.send(json.dumps({"id": 2, "method": "Page.reload", "params": {"ignoreCache": True}}))
                await asyncio.wait_for(ws.recv(), 10)
            return True
        except Exception as e:
            log(f"stage: in-place refresh of {tid} failed ({e}), opening a new tab")
            return False

    async def _show_locked(self, mine, owner, url, purpose, dwell):
        self.holder, self.url, self.purpose = owner, url, purpose
        self.until = time.time() + dwell

        # Matt's rule, 2026-09-16: re-showing a page closes the old tab first, so
        # there is never a row of half-stale copies of the same page to pick from.
        # Only ever THIS OWNER's previous stage tab. 2026-10-02: the stage kept one
        # tab for everybody, so each agent's show closed the page another agent had
        # just put in front of Matt (page-audit and email-session ate each other's
        # tabs every few minutes). A tab the human opened is never ours either.
        # Matt's rule, 2026-10-22: "refresh the page that is already open, never open
        # new tabs of the same thing." If any tab already shows this page (same URL
        # minus the #fragment, whoever opened it), reload that tab in place and close
        # this owner's other stage tab, instead of stacking another copy.
        same = await self._tab_showing(url)
        prev = self.tabs.pop(owner, None)
        if prev and prev != same:
            try:
                await self.up.send("Target.closeTarget", targetId=prev)
            except Exception:
                pass

        if same and await self._navigate(same, url):
            for o, t in list(self.tabs.items()):
                if t == same:
                    self.tabs.pop(o, None)
            self.tabs[owner] = same
        else:
            res = await self.up.send("Target.createTarget", url=url, background=False)
            self.tabs[owner] = res["targetId"]
        await self._front(self.tabs[owner])
        log(f"stage -> {owner}: {url} ({purpose or 'no purpose given'}), held {dwell}s")
        asyncio.create_task(self._decay(mine, dwell))
        return {"shown": url, "target_id": self.tabs[owner], "held_for": dwell, "owner": owner}

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
                await REG.close(tid, "client closed it")
                await reply(m, result={"success": True})
                return None
        if method == "Target.setAutoAttach" and p.get("flatten") is not None:
            pass    # allowed; auto-attach events are filtered below
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
                            "target": t["target"], "expires_in": t["expires_in"]}
                           for t in s["tabs"] if t["kind"] == "rest"]
            return self._reply(200, s)
        if self.path.startswith("/health"):
            return self._reply(200, {"ok": True, "brave": self._run(UP.alive())})
        if self.path.startswith("/stage"):
            return self._reply(200, STAGE.state())
        self._reply(404, {"error": "no such endpoint"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            b = json.loads(self.rfile.read(n) or b"{}")
            if self.path.startswith("/lease"):
                ttl = int(b.get("ttl", DEFAULT_TTL))
                tid = self._run(REG.create(b.get("owner", "anonymous"), b.get("url"),
                                           hidden=bool(b.get("hidden", True)), kind="rest", ttl=ttl))
                i = REG.tabs[tid]
                i["attached"] = 1          # a REST lease counts as held from grant
                i["purpose"] = b.get("purpose")
                return self._reply(200, {"lease_id": i["lease_id"], "target_id": tid,
                                         "ws_url": f"ws://127.0.0.1:{LISTEN}/devtools/page/{tid}",
                                         "expires_in": ttl})
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
                        self._run(REG.close(tid, "released"))
                        return self._reply(200, {"released": b["lease_id"]})
                return self._reply(404, {"error": "unknown lease"})
            if self.path.startswith("/show"):
                if not b.get("url"):
                    return self._reply(400, {"error": "show needs a url"})
                try:
                    wait = STAGE_WAIT if b.get("wait") is None else int(b["wait"])
                    return self._reply(200, self._run(STAGE.show(
                        b.get("owner", "anonymous"), b["url"], b.get("purpose"),
                        wait, b.get("dwell")), timeout=wait + 15))
                except StageBusy as e:
                    return self._reply(409, {"error": str(e), "held_by": e.holder,
                                             "seconds_left": e.seconds_left, "showing": e.url})
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
    held = []
    for port in (LISTEN, API):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            for h in held:
                h.close()
            s.close()
            log(f"another proxy already owns port {port} — exiting without "
                f"touching any tabs")
            sys.exit(0)
        held.append(s)
    for h in held:          # hand the ports back to the real servers below
        h.close()


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
