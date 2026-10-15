"""Shared fixtures: a fake browser plus the real proxy.py, wired together on
ephemeral loopback ports, with the event loop running in a background thread
so tests can talk to both with ordinary blocking clients."""
import asyncio
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Everything in these tests is 127.0.0.1. A proxy configured in the developer's
# environment must not get in the way of urllib or the websocket clients.
for _k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from websockets.asyncio.server import serve as ws_serve  # noqa: E402
import websocket  # websocket-client, the library broker_client.py uses  # noqa: E402

from fake_browser import FakeBrowser  # noqa: E402


class LoopThread:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro, timeout=10):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def stop(self):
        async def cancel_rest():
            me = asyncio.current_task()
            rest = [t for t in asyncio.all_tasks() if t is not me]
            for t in rest:
                t.cancel()
            await asyncio.gather(*rest, return_exceptions=True)
        try:
            self.run(cancel_rest(), timeout=5)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)
            if not self.thread.is_alive():
                self.loop.close()


def http(method, url, body=None, timeout=10):
    """(status, parsed body) for a plain HTTP call; never raises on 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code, ctype = r.read(), r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raw, code, ctype = e.read(), e.code, e.headers.get("Content-Type", "")
    if "json" in ctype:
        return code, json.loads(raw)
    return code, raw.decode()


class CDPClient:
    """A blocking CDP client over one websocket, collecting events as it goes."""

    def __init__(self, url):
        self.ws = websocket.create_connection(url, suppress_origin=True, timeout=5)
        self._id = 0
        self.events = []

    def send(self, method, session_id=None, **params):
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params}
        if session_id:
            msg["sessionId"] = session_id
        self.ws.send(json.dumps(msg))
        return self._id

    def call(self, method, session_id=None, timeout=5, **params):
        """Send and wait for the reply. Returns the whole reply message."""
        mid = self.send(method, session_id, **params)
        deadline = time.time() + timeout
        while True:
            m = json.loads(self._recv(max(0.01, deadline - time.time())))
            if m.get("id") == mid:
                return m
            self.events.append(m)

    def drain(self, seconds=0.3):
        """Collect whatever events arrive in the next `seconds`."""
        deadline = time.time() + seconds
        while True:
            left = deadline - time.time()
            if left <= 0:
                return self.events
            try:
                self.events.append(json.loads(self._recv(left)))
            except websocket.WebSocketTimeoutException:
                return self.events

    def _recv(self, timeout):
        self.ws.settimeout(timeout)
        return self.ws.recv()

    def close_code(self, timeout=3):
        """Wait for the server to close the socket; return its close code."""
        self.ws.settimeout(timeout)
        while True:
            op, data = self.ws.recv_data(control_frame=True)
            if op == websocket.ABNF.OPCODE_CLOSE:
                return int.from_bytes(data[:2], "big") if len(data) >= 2 else None

    def event_methods(self):
        return [e.get("method") for e in self.events]

    def close(self):
        self.ws.close()


class Stack:
    """The fake browser and a fully wired proxy.py, as main() would set it up."""

    def __init__(self, tmp_path):
        import proxy
        self.proxy = proxy
        self.lt = LoopThread()
        self.clients = []
        self.fake = self.lt.run(FakeBrowser().start())
        self._saved = {k: getattr(proxy, k) for k in
                       ("UPSTREAM", "LISTEN", "LOG", "STATE", "REG", "UP", "STAGE", "INNER_PORT")}
        proxy.UPSTREAM = self.fake.port
        proxy.LOG = str(tmp_path / "broker.log")
        proxy.STATE = str(tmp_path / "tabs.json")
        self.lt.run(self._start())

    async def _start(self):
        p = self.proxy
        p.UP = p.Upstream()
        await p.UP.connect()
        p.REG = p.Registry(p.UP)
        await p.REG.close_leftovers()
        p.STAGE = p.Stage(p.UP)
        self.inner = await ws_serve(p.handle_ws, "127.0.0.1", 0, process_request=p.process_request,
                                    max_size=None, ping_interval=None)
        p.INNER_PORT = self.inner.sockets[0].getsockname()[1]
        self.front = await asyncio.start_server(p.front_door, "127.0.0.1", 0, limit=1 << 20)
        self.port = self.front.sockets[0].getsockname()[1]
        p.LISTEN = self.port
        p.ApiHandler.loop = asyncio.get_running_loop()
        self.api = ThreadingHTTPServer(("127.0.0.1", 0), p.ApiHandler)
        self.api_port = self.api.server_address[1]
        threading.Thread(target=self.api.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()

    async def _stop(self):
        # close(), not wait_closed(): a test may leave a socket open on purpose,
        # and the loop is thrown away right after this anyway.
        self.front.close()
        self.inner.close()
        self.fake._server.close()
        await asyncio.sleep(0.05)

    # ---- conveniences ------------------------------------------------------
    def run(self, coro, timeout=10):
        return self.lt.run(coro, timeout)

    @property
    def reg(self):
        return self.proxy.REG

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def api_base(self):
        return f"http://127.0.0.1:{self.api_port}"

    def browser_client(self):
        c = CDPClient(f"ws://127.0.0.1:{self.port}/devtools/browser/anything")
        self.clients.append(c)
        return c

    def page_client(self, tid):
        c = CDPClient(f"ws://127.0.0.1:{self.port}/devtools/page/{tid}")
        self.clients.append(c)
        return c

    def wait_for(self, predicate, timeout=3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def close(self):
        for c in self.clients:
            try:
                c.close()
            except Exception:
                pass
        self.api.shutdown()
        self.api.server_close()
        try:
            self.run(self._stop())
        finally:
            self.lt.stop()
            for k, v in self._saved.items():
                setattr(self.proxy, k, v)


@pytest.fixture
def stack(tmp_path):
    s = Stack(tmp_path)
    try:
        yield s
    finally:
        s.close()
