#!/usr/bin/env python3
"""browser-broker — one owner for the logged-in Brave, so agents stop fighting.

Every agent on this machine drives the SAME Brave through CDP on 9222, and CDP
has no concept of ownership: two agents that both go looking for a tab land on
the same one, and one navigating it out from under the other is invisible to
both. Worse, an agent that grabs "the active tab" grabs whatever the human just
clicked on.

This process owns 9222. Agents ask it for a LEASE and get an exclusive claim on
a target they alone may touch.

The ownership rule is deliberately dumb, because clever focus-detection breaks
across macOS Spaces: **the broker can only hand out tabs the broker opened.**
Anything the human opened is invisible to every agent, permanently, with no
heuristics involved. There is an /adopt escape hatch for the rare "drive this
existing tab" case, and it requires saying so explicitly.

Leases expire, so a crashed agent releases its tab instead of deadlocking the
browser forever. Work tabs are ordinary BACKGROUND tabs in the window the human
already has open — never a separate window, never headless. Launch the browser
with --disable-backgrounding-occluded-windows and --disable-renderer-backgrounding
(see launch-browser.sh): Chromium throttles background-tab timers to about 1Hz, and
a throttled tab mounts zero rows in a modern SPA. The flags keep timers and fetch
callbacks running; they do NOT make a hidden tab paint. requestAnimationFrame never
fires while visibilityState is "hidden", so a rAF-driven page (canvas, animated
charts) stays on its first frame no matter what flags you pass.

Listens on 127.0.0.1:9223. Never exposed off the box.
"""
import json, threading, time, uuid, sys, os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen
from urllib.error import URLError

from websocket import create_connection, WebSocketException

# Everything is overridable by environment so the same file runs anywhere.
CDP_PORT = int(os.environ.get("BROKER_CDP_PORT", 9222))
LISTEN = ("127.0.0.1", int(os.environ.get("BROKER_PORT", 9223)))
DEFAULT_TTL = int(os.environ.get("BROKER_TTL", 300))          # seconds an unrenewed lease survives
REAP_EVERY = 5                                                  # seconds between expiry sweeps
HEARTBEAT_EVERY = int(os.environ.get("BROKER_HEARTBEAT", 3600)) # one proof-of-life line per hour
LOG = os.path.expanduser(os.environ.get("BROKER_LOG",
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker.log")))


def log(msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


class CDPError(Exception):
    pass


class Browser:
    """Browser-level CDP connection, reconnecting on its own.

    Brave dies under us regularly — it auto-updates and gets restarted by
    brave-update-fixer. Every call goes through _send, which reconnects once
    before giving up, so a restart costs a retry rather than a dead broker.
    """

    def __init__(self):
        self._ws = None
        self._id = 0
        self._lock = threading.Lock()

    def _ws_url(self):
        with urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=5) as r:
            return json.load(r)["webSocketDebuggerUrl"]

    def _connect(self):
        self._ws = create_connection(self._ws_url(), suppress_origin=True, timeout=20)

    def _send_once(self, method, **params):
        if self._ws is None:
            self._connect()
        self._id += 1
        mid = self._id
        self._ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self._ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(msg["error"].get("message", str(msg["error"])))
                return msg.get("result", {})

    def send(self, method, **params):
        with self._lock:
            try:
                return self._send_once(method, **params)
            except (WebSocketException, OSError, URLError, json.JSONDecodeError):
                try:
                    if self._ws:
                        self._ws.close()
                except Exception:
                    pass
                self._ws = None
                return self._send_once(method, **params)

    def alive(self):
        try:
            self.send("Browser.getVersion")
            return True
        except Exception:
            return False

    def targets(self):
        return {t["targetId"] for t in self.send("Target.getTargets").get("targetInfos", [])
                if t.get("type") == "page"}


class Registry:
    """Which targets the broker opened, and who currently holds each one."""

    def __init__(self, browser):
        self.b = browser
        self.lock = threading.RLock()
        self.owned = {}    # target_id -> {"created": ts, "hidden": bool}
        self.leases = {}   # lease_id -> {target_id, owner, purpose, expires}

    # ---- leases --------------------------------------------------------
    def open_lease(self, owner, url, purpose, ttl, hidden=True):
        with self.lock:
            # An ordinary background tab in the window that is already open.
            # This used to be a new window parked at x=-4200. It rendered fine
            # and never took focus, but the window still EXISTED, so every agent
            # run shoved the human's window aside -- twice over, with a broker
            # on two machines. Isolation was never the window's job: the
            # ownership registry plus the swallowed focus commands do that.
            # background=True opens it behind whatever the human is looking at.
            res = self.b.send("Target.createTarget", url=url or "about:blank",
                              background=True)
            tid = res["targetId"]
            self.owned[tid] = {"created": time.time(), "hidden": hidden}
            return self._grant(tid, owner, purpose, ttl)

    def adopt_lease(self, owner, target_id, purpose, ttl):
        """Explicitly take over a tab the broker did not open. Deliberate only."""
        with self.lock:
            if target_id not in self.b.targets():
                raise KeyError("no such target")
            if self._holder(target_id):
                raise PermissionError("target already leased")
            self.owned[target_id] = {"created": time.time(), "hidden": False,
                                     "adopted": True}
            return self._grant(target_id, owner, purpose, ttl)

    def _grant(self, tid, owner, purpose, ttl):
        lid = uuid.uuid4().hex[:12]
        self.leases[lid] = {"target_id": tid, "owner": owner, "purpose": purpose,
                            "expires": time.time() + ttl}
        log(f"lease {lid} -> {owner} on {tid[:12]} ({purpose or 'no purpose given'})")
        return {"lease_id": lid, "target_id": tid,
                "ws_url": f"ws://127.0.0.1:{CDP_PORT}/devtools/page/{tid}",
                "expires_in": ttl}

    def _holder(self, tid):
        for lid, l in self.leases.items():
            if l["target_id"] == tid and l["expires"] > time.time():
                return lid
        return None

    def renew(self, lease_id, ttl):
        with self.lock:
            l = self.leases.get(lease_id)
            if not l:
                raise KeyError("unknown lease")
            l["expires"] = time.time() + ttl
            return {"lease_id": lease_id, "expires_in": ttl}

    def release(self, lease_id, close=True):
        with self.lock:
            l = self.leases.pop(lease_id, None)
            if not l:
                raise KeyError("unknown lease")
            tid = l["target_id"]
            if close and tid in self.owned and not self.owned[tid].get("adopted"):
                try:
                    self.b.send("Target.closeTarget", targetId=tid)
                except CDPError:
                    pass
                self.owned.pop(tid, None)
            log(f"lease {lease_id} released by {l['owner']}")
            return {"released": lease_id}

    def reap(self):
        """Expire stale leases. A crashed agent must not hold a tab forever."""
        with self.lock:
            now = time.time()
            dead = [lid for lid, l in self.leases.items() if l["expires"] <= now]
            for lid in dead:
                l = self.leases[lid]
                log(f"lease {lid} EXPIRED (owner {l['owner']}) — reclaiming tab")
                try:
                    self.release(lid, close=True)
                except KeyError:
                    pass
            # Drop bookkeeping for tabs that vanished (Brave restart, manual close).
            if dead or self.owned:
                try:
                    live = self.b.targets()
                except Exception:
                    return
                for tid in list(self.owned):
                    if tid not in live:
                        self.owned.pop(tid, None)

    def status(self):
        with self.lock:
            now = time.time()
            return {
                "leases": [{"lease_id": lid, "owner": l["owner"], "purpose": l["purpose"],
                            "target": l["target_id"][:12],
                            "expires_in": round(l["expires"] - now, 1)}
                           for lid, l in self.leases.items()],
                "broker_tabs": len(self.owned),
                "brave_alive": self.b.alive(),
            }


class Handler(BaseHTTPRequestHandler):
    registry = None

    def log_message(self, *a):
        pass  # our own log() only

    def _reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path.startswith("/status"):
            return self._reply(200, self.registry.status())
        if self.path.startswith("/health"):
            return self._reply(200, {"ok": True, "brave": self.registry.b.alive()})
        self._reply(404, {"error": "no such endpoint"})

    def do_POST(self):
        try:
            b = self._body()
            r = self.registry
            if self.path.startswith("/lease"):
                return self._reply(200, r.open_lease(
                    b.get("owner", "anonymous"), b.get("url"), b.get("purpose"),
                    int(b.get("ttl", DEFAULT_TTL)), bool(b.get("hidden", True))))
            if self.path.startswith("/adopt"):
                return self._reply(200, r.adopt_lease(
                    b.get("owner", "anonymous"), b["target_id"], b.get("purpose"),
                    int(b.get("ttl", DEFAULT_TTL))))
            if self.path.startswith("/renew"):
                return self._reply(200, r.renew(b["lease_id"], int(b.get("ttl", DEFAULT_TTL))))
            if self.path.startswith("/release"):
                return self._reply(200, r.release(b["lease_id"], bool(b.get("close", True))))
            self._reply(404, {"error": "no such endpoint"})
        except KeyError as e:
            self._reply(404, {"error": str(e)})
        except PermissionError as e:
            self._reply(409, {"error": str(e)})
        except Exception as e:
            self._reply(500, {"error": f"{type(e).__name__}: {e}"})


def background(registry):
    """Reaper + heartbeat. The heartbeat is not decoration: brave-update-fixer
    sat loaded and dead for six weeks because a silent exit 0 looks exactly like
    a healthy one. Silence in this log means the broker itself died."""
    last_hb = 0
    while True:
        try:
            registry.reap()
            if time.time() - last_hb > HEARTBEAT_EVERY:
                s = registry.status()
                log(f"heartbeat — {len(s['leases'])} leases, {s['broker_tabs']} broker tabs, "
                    f"brave {'up' if s['brave_alive'] else 'DOWN'}")
                last_hb = time.time()
        except Exception as e:
            log(f"background error: {type(e).__name__}: {e}")
        time.sleep(REAP_EVERY)


def main():
    b = Browser()
    if not b.alive():
        log(f"no browser answering CDP on {CDP_PORT} — launch Chrome/Brave with "
            f"--remote-debugging-port={CDP_PORT} first (see launch-browser.sh)")
        sys.exit(1)
    reg = Registry(b)
    Handler.registry = reg
    threading.Thread(target=background, args=(reg,), daemon=True).start()
    srv = ThreadingHTTPServer(LISTEN, Handler)
    log(f"broker up on http://{LISTEN[0]}:{LISTEN[1]} — owning CDP {CDP_PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
