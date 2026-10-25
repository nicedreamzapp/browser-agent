"""A small stand-in for Chrome's remote-debugging port.

It speaks just enough of the Chrome DevTools Protocol for proxy.py to run
against it: the /json/version and /json/protocol HTTP endpoints, a
browser-level websocket with the Target.* commands the proxy relies on, and
page websockets. Every command it receives is recorded in `calls`, so a test
can assert that something the proxy should swallow never reached the browser.

No real browser, no network beyond 127.0.0.1, no OS-specific APIs.
"""
import asyncio
import itertools
import json
import re
import uuid

from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response


def _json_response(obj):
    body = json.dumps(obj).encode()
    return Response(200, "OK", Headers([("Content-Type", "application/json"),
                                        ("Content-Length", str(len(body)))]), body)


class _Conn:
    def __init__(self, ws):
        self.ws = ws
        self.discover = False
        self.auto_attach = False
        self.sessions = {}          # sessionId -> targetId


class FakeBrowser:
    def __init__(self):
        self.targets = {}            # targetId -> targetInfo
        self.contexts = set()
        self.calls = []              # (channel, method, params)
        self.conns = []
        self.port = None
        self._server = None
        self._sid = itertools.count(1)
        self.fail = {}               # method -> error message, to simulate failures
        self.page_text = {}          # targetId -> visible text a peek should read back
        self.page_fields = {}        # targetId -> [{"field", "value"}] a peek should read back
        self.page_events = {}        # targetId -> [[t, kind]] the in-page listener has recorded
        self.watchers = {}           # targetId -> [page sockets that added the broker's binding]
        self.visible = set()         # targetIds whose document.visibilityState reads "visible"

    # ---- test helpers ----------------------------------------------------
    def add_human_tab(self, url="https://mail.example.com/inbox", title="Inbox"):
        """A tab the human opened. The proxy must never show it to an agent."""
        tid = uuid.uuid4().hex.upper()
        self.targets[tid] = {"targetId": tid, "type": "page", "title": title, "url": url,
                             "attached": False, "canAccessOpener": False,
                             "browserContextId": "DEFAULT"}
        return tid

    async def human(self, tid, kind="keydown", t=None):
        """Simulate the person typing or clicking in tab `tid`: the in-page listener
        records it and calls the broker's binding, exactly as the injected script does."""
        import time
        t = time.time() if t is None else t
        self.page_events.setdefault(tid, []).append([t, kind])
        payload = json.dumps([t, kind])
        for ws in list(self.watchers.get(tid, [])):
            await ws.send(json.dumps({"method": "Runtime.bindingCalled",
                                      "params": {"name": "__bbHumanEvent", "payload": payload,
                                                 "executionContextId": 1}}))
        return t

    def methods(self, channel=None):
        return [m for c, m, _ in self.calls if channel is None or c == channel]

    def params_for(self, method):
        return [p for _, m, p in self.calls if m == method]

    # ---- server ----------------------------------------------------------
    async def start(self):
        self._server = await serve(self._handle, "127.0.0.1", 0,
                                   process_request=self._http, max_size=None,
                                   ping_interval=None)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        self._server.close()
        await self._server.wait_closed()

    def _http(self, connection, request):
        path = request.path.rstrip("/")
        if path == "/json/version":
            return _json_response({
                "Browser": "FakeChrome/1.0",
                "Protocol-Version": "1.3",
                "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/browser/fake",
            })
        if path == "/json/protocol":
            return _json_response({"version": {"major": "1", "minor": "3"}, "domains": []})
        if path in ("/json", "/json/list"):
            return _json_response(list(self.targets.values()))
        return None   # a websocket upgrade

    async def _handle(self, ws):
        path = ws.request.path
        if path.startswith("/devtools/page/"):
            await self._page(ws, path.rsplit("/", 1)[-1])
        else:
            await self._browser(ws)

    async def _page(self, ws, tid):
        if tid not in self.targets:
            await ws.close(1008, "no such target")
            return
        # The broker's human-activity watcher holds one page socket open per tab
        # and opens with Runtime.addBinding. Its traffic is recorded on its own
        # channel, "watch:<tid>", so tests about what agents send stay exact.
        channel = f"page:{tid}"
        try:
            async for raw in ws:
                m = json.loads(raw)
                params = m.get("params") or {}
                if m.get("method") == "Runtime.addBinding" and params.get("name") == "__bbHumanEvent":
                    channel = f"watch:{tid}"
                    self.watchers.setdefault(tid, []).append(ws)
                self.calls.append((channel, m.get("method"), params))
                await self._page_reply(ws, tid, m)
        finally:
            if ws in self.watchers.get(tid, []):
                self.watchers[tid].remove(ws)

    async def _page_reply(self, ws, tid, m):
        expr = (m.get("params") or {}).get("expression", "")
        if m.get("method") == "Page.navigate" and tid in self.targets:
            self.targets[tid]["url"] = (m.get("params") or {}).get("url", "")
        if m.get("method") == "Runtime.evaluate" and "visibilityState" in expr and tid in self.targets:
            result = {"result": {"type": "string", "value": json.dumps(
                {"visibility": "visible" if tid in self.visible else "hidden",
                 "human": self.page_events.get(tid, [])})}}
        elif m.get("method") == "Runtime.evaluate" and "innerText" in expr and tid in self.targets:
            t = self.targets[tid]
            cut = re.search(r"const n = (\d+);", expr)
            text = self.page_text.get(tid, "")[:int(cut.group(1)) if cut else None]
            result = {"result": {"type": "string", "value": json.dumps(
                {"url": t["url"], "title": t["title"], "text": text,
                 "fields": self.page_fields.get(tid, []),
                 "human": self.page_events.get(tid, [])})}}
        elif m.get("method") == "Runtime.evaluate" and "__bbHuman.events" in expr and tid in self.targets:
            result = {"result": {"type": "string", "value": json.dumps(self.page_events.get(tid, []))}}
        elif m.get("method") == "Runtime.evaluate":
            result = {"result": {"type": "string",
                                 "value": f"evaluated {m['params']['expression']} in {tid}"}}
        else:
            result = {"echo": m.get("method"), "targetId": tid}
        out = {"id": m.get("id"), "result": result}
        if "sessionId" in m:
            out["sessionId"] = m["sessionId"]
        await ws.send(json.dumps(out))

    async def _emit(self, conn, method, params, session_id=None):
        msg = {"method": method, "params": params}
        if session_id:
            msg["sessionId"] = session_id
        try:
            await conn.ws.send(json.dumps(msg))
        except Exception:
            pass

    async def _attach(self, conn, tid):
        sid = f"S{next(self._sid)}"
        conn.sessions[sid] = tid
        await self._emit(conn, "Target.attachedToTarget",
                         {"sessionId": sid, "targetInfo": self.targets[tid],
                          "waitingForDebugger": False})
        return sid

    async def _browser(self, ws):
        conn = _Conn(ws)
        self.conns.append(conn)
        try:
            async for raw in ws:
                m = json.loads(raw)
                method, p = m.get("method"), m.get("params") or {}
                self.calls.append(("browser", method, p))
                out = {"id": m.get("id")}
                if "sessionId" in m:
                    out["sessionId"] = m["sessionId"]
                try:
                    if method in self.fail:
                        raise RuntimeError(self.fail[method])
                    if "sessionId" in m:
                        if m["sessionId"] not in conn.sessions:
                            raise RuntimeError("Session with given id not found.")
                        out["result"] = {"echo": method}
                    else:
                        out["result"] = await self._command(conn, method, p)
                except RuntimeError as e:
                    out.pop("result", None)
                    out["error"] = {"code": -32000, "message": str(e)}
                if m.get("id"):     # id 0 is a fire-and-forget from the proxy
                    await ws.send(json.dumps(out))
        finally:
            self.conns.remove(conn)

    async def _command(self, conn, method, p):
        if method == "Browser.getVersion":
            return {"product": "FakeChrome/1.0", "protocolVersion": "1.3"}
        if method == "Target.getTargets":
            return {"targetInfos": [dict(t) for t in self.targets.values()]}
        if method == "Target.getTargetInfo":
            tid = p.get("targetId")
            if tid not in self.targets:
                raise RuntimeError("No target with given id found")
            return {"targetInfo": self.targets[tid]}
        if method == "Target.createTarget":
            ctx = p.get("browserContextId", "DEFAULT")
            if ctx != "DEFAULT" and ctx not in self.contexts:
                raise RuntimeError("Failed to find browser context with id " + ctx)
            tid = uuid.uuid4().hex.upper()
            self.targets[tid] = {"targetId": tid, "type": "page", "title": "",
                                 "url": p.get("url", "about:blank"), "attached": False,
                                 "canAccessOpener": False, "browserContextId": ctx}
            for c in list(self.conns):
                if c.discover:
                    await self._emit(c, "Target.targetCreated", {"targetInfo": self.targets[tid]})
                if c.auto_attach:
                    await self._attach(c, tid)
            return {"targetId": tid}
        if method == "Target.closeTarget":
            tid = p.get("targetId")
            if tid not in self.targets:
                raise RuntimeError("No target with given id found")
            await self._destroy(tid)
            return {"success": True}
        if method == "Target.setDiscoverTargets":
            conn.discover = bool(p.get("discover"))
            if conn.discover:
                for t in list(self.targets.values()):
                    await self._emit(conn, "Target.targetCreated", {"targetInfo": t})
            return {}
        if method == "Target.setAutoAttach":
            conn.auto_attach = bool(p.get("autoAttach"))
            if conn.auto_attach:
                for tid in list(self.targets):
                    await self._attach(conn, tid)
            return {}
        if method == "Target.attachToTarget":
            tid = p.get("targetId")
            if tid not in self.targets:
                raise RuntimeError("No target with given id found")
            return {"sessionId": await self._attach(conn, tid)}
        if method == "Target.detachFromTarget":
            sid = p.get("sessionId")
            if sid not in conn.sessions:
                raise RuntimeError("No session with given id")
            conn.sessions.pop(sid)
            await self._emit(conn, "Target.detachedFromTarget", {"sessionId": sid})
            return {}
        if method == "Target.createBrowserContext":
            ctx = "CTX" + uuid.uuid4().hex[:8].upper()
            self.contexts.add(ctx)
            return {"browserContextId": ctx}
        if method == "Target.disposeBrowserContext":
            ctx = p.get("browserContextId")
            if ctx not in self.contexts:
                raise RuntimeError("Failed to find context with id " + str(ctx))
            self.contexts.discard(ctx)
            for tid in [t for t, i in self.targets.items() if i["browserContextId"] == ctx]:
                await self._destroy(tid)
            return {}
        # Target.activateTarget, Browser.close, etc.: accepted and recorded.
        return {}

    async def _destroy(self, tid):
        self.targets.pop(tid, None)
        for c in list(self.conns):
            for sid, t in list(c.sessions.items()):
                if t == tid:
                    c.sessions.pop(sid)
                    await self._emit(c, "Target.detachedFromTarget",
                                     {"sessionId": sid, "targetId": tid})
            if c.discover:
                await self._emit(c, "Target.targetDestroyed", {"targetId": tid})


async def _main():   # pragma: no cover - handy for poking at it by hand
    fb = await FakeBrowser().start()
    fb.add_human_tab()
    print(f"fake browser on {fb.port}")
    await asyncio.Future()


if __name__ == "__main__":   # pragma: no cover
    asyncio.run(_main())
