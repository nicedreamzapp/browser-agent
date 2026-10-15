"""broker.py, the original standalone lease broker (kept for reference)."""
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

import broker
from conftest import http


class FakeBrowser:
    """Stands in for broker.Browser: a set of page targets and a call log."""

    def __init__(self, targets=()):
        self.live = set(targets)
        self.calls = []
        self._n = 0
        self.dead = False

    def send(self, method, **params):
        self.calls.append((method, params))
        if method == "Target.createTarget":
            self._n += 1
            tid = f"T{self._n:031d}"
            self.live.add(tid)
            return {"targetId": tid}
        if method == "Target.closeTarget":
            if params["targetId"] not in self.live:
                raise broker.CDPError("No target with given id found")
            self.live.discard(params["targetId"])
            return {"success": True}
        return {}

    def targets(self):
        if self.dead:
            raise ConnectionError("browser down")
        return set(self.live)

    def alive(self):
        return not self.dead


@pytest.fixture(autouse=True)
def quiet_log(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "LOG", str(tmp_path / "broker.log"))


@pytest.fixture
def reg():
    return broker.Registry(FakeBrowser(targets={"HUMAN"}))


def test_open_lease_makes_a_background_tab(reg):
    g = reg.open_lease("a", "https://x", "p", 60)
    assert reg.b.calls == [("Target.createTarget", {"url": "https://x", "background": True})]
    assert g["expires_in"] == 60 and g["target_id"] in reg.owned
    assert g["ws_url"] == f"ws://127.0.0.1:{broker.CDP_PORT}/devtools/page/{g['target_id']}"


def test_release_closes_owned_tab(reg):
    g = reg.open_lease("a", None, None, 60)
    assert reg.release(g["lease_id"]) == {"released": g["lease_id"]}
    assert g["target_id"] not in reg.b.live and g["target_id"] not in reg.owned
    with pytest.raises(KeyError):
        reg.release(g["lease_id"])


def test_release_without_close_keeps_tab(reg):
    g = reg.open_lease("a", None, None, 60)
    reg.release(g["lease_id"], close=False)
    assert g["target_id"] in reg.b.live


def test_adopt_rules(reg):
    with pytest.raises(KeyError):
        reg.adopt_lease("a", "NOPE", None, 60)
    g = reg.adopt_lease("a", "HUMAN", "deliberate", 60)
    with pytest.raises(PermissionError):
        reg.adopt_lease("b", "HUMAN", None, 60)
    # releasing an adopted tab never closes the human's tab
    reg.release(g["lease_id"])
    assert "HUMAN" in reg.b.live


def test_adopt_after_expiry_is_allowed(reg):
    g = reg.adopt_lease("a", "HUMAN", None, 60)
    reg.leases[g["lease_id"]]["expires"] = time.time() - 1
    reg.adopt_lease("b", "HUMAN", None, 60)


def test_renew(reg):
    g = reg.open_lease("a", None, None, 1)
    assert reg.renew(g["lease_id"], 500)["expires_in"] == 500
    assert reg.leases[g["lease_id"]]["expires"] > time.time() + 400
    with pytest.raises(KeyError):
        reg.renew("nope", 5)


def test_reap_expires_and_forgets_vanished(reg):
    g1 = reg.open_lease("a", None, None, 60)
    g2 = reg.open_lease("b", None, None, 60)
    reg.leases[g1["lease_id"]]["expires"] = time.time() - 1
    reg.b.live.discard(g2["target_id"])        # closed by hand
    reg.reap()
    assert g1["lease_id"] not in reg.leases and g1["target_id"] not in reg.b.live
    assert g2["target_id"] not in reg.owned
    assert "HUMAN" in reg.b.live


def test_reap_survives_browser_down(reg):
    reg.open_lease("a", None, None, 60)
    reg.b.dead = True
    reg.reap()
    assert len(reg.owned) == 1


def test_status(reg):
    reg.open_lease("a", None, "why", 60)
    s = reg.status()
    assert s["broker_tabs"] == 1 and s["brave_alive"] is True
    assert s["leases"][0]["owner"] == "a" and s["leases"][0]["purpose"] == "why"


@pytest.fixture
def api(reg):
    broker.Handler.registry = reg
    srv = ThreadingHTTPServer(("127.0.0.1", 0), broker.Handler)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", reg
    srv.shutdown()
    srv.server_close()


def test_http_api(api):
    base, reg = api
    code, g = http("POST", base + "/lease", {"owner": "a", "ttl": 30})
    assert code == 200 and g["expires_in"] == 30
    assert http("POST", base + "/renew", {"lease_id": g["lease_id"]})[0] == 200
    assert http("GET", base + "/status")[1]["broker_tabs"] == 1
    assert http("GET", base + "/health") == (200, {"ok": True, "brave": True})
    assert http("POST", base + "/release", {"lease_id": g["lease_id"]})[0] == 200
    assert http("POST", base + "/release", {"lease_id": g["lease_id"]})[0] == 404
    assert http("POST", base + "/adopt", {"target_id": "NOPE"})[0] == 404
    assert http("POST", base + "/adopt", {"target_id": "HUMAN"})[0] == 200
    assert http("POST", base + "/adopt", {"target_id": "HUMAN"})[0] == 409
    assert http("POST", base + "/nope", {})[0] == 404
    assert http("GET", base + "/nope")[0] == 404


def test_http_api_bad_ttl_is_500(api):
    base, _ = api
    code, r = http("POST", base + "/lease", {"ttl": "soon"})
    assert code == 500 and "ValueError" in r["error"]


class FakeWS:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def send(self, raw):
        self.sent.append(raw)

    def recv(self):
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        pass


def test_browser_send_skips_events_and_raises_cdp_errors(monkeypatch):
    b = broker.Browser()
    b._ws = FakeWS(['{"method": "Target.targetCreated"}', '{"id": 1, "result": {"ok": 1}}',
                    '{"id": 2, "error": {"message": "nope"}}'])
    assert b.send("A") == {"ok": 1}
    with pytest.raises(broker.CDPError, match="nope"):
        b.send("B")


def test_browser_reconnects_once(monkeypatch):
    from websocket import WebSocketException
    b = broker.Browser()
    b._ws = FakeWS([WebSocketException("dropped")])
    fresh = FakeWS(['{"id": 2, "result": {"again": true}}'])

    def connect():
        b._ws = fresh
    monkeypatch.setattr(b, "_connect", connect)
    assert b.send("X") == {"again": True}
    assert b.alive() is False        # the fresh socket has no more replies -> IndexError


def test_browser_targets_filters_pages():
    b = broker.Browser()
    b._ws = FakeWS(['{"id": 1, "result": {"targetInfos": ['
                    '{"targetId": "P", "type": "page"}, {"targetId": "W", "type": "service_worker"}]}}'])
    assert b.targets() == {"P"}
