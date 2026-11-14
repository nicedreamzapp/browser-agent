"""broker_client.py against the real proxy and the fake browser."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import broker_client


@pytest.fixture
def client(stack, monkeypatch):
    monkeypatch.setattr(broker_client, "BROKER", stack.api_base)
    return stack


def test_lease_drives_its_own_tab_and_releases_it(client):
    human = client.fake.add_human_tab()
    with broker_client.lease("reaper", url="https://mail.example", purpose="sweep") as tab:
        (tid,) = [t for t in client.reg.tabs]
        assert tab.eval("document.title") == f"evaluated document.title in {tid}"
        assert client.reg.tabs[tid]["owner"] == "reaper"
    assert client.wait_for(lambda: tid not in client.fake.targets)
    assert client.reg.tabs == {}
    assert human in client.fake.targets


def test_lease_releases_on_exception(client):
    with pytest.raises(ValueError):
        with broker_client.lease("crashy"):
            raise ValueError("agent blew up")
    assert client.wait_for(lambda: client.reg.tabs == {})


def test_tab_send_and_goto(client, monkeypatch):
    with broker_client.lease("nav") as tab:
        (tid,) = list(client.reg.tabs)
        tab.goto("https://example.com", wait=0)
        assert client.fake.methods(f"page:{tid}") == ["Page.enable", "Page.navigate"]
        assert client.fake.params_for("Page.navigate") == [{"url": "https://example.com"}]


def test_tab_send_raises_cdp_errors(client):
    with broker_client.lease("x") as tab:
        tab.ws.close()
    t = broker_client.Tab.__new__(broker_client.Tab)

    class WS:
        def send(self, raw):
            self.mid = json.loads(raw)["id"]

        def recv(self):
            return json.dumps({"id": self.mid, "error": {"message": "nope"}})
    t.ws, t._id = WS(), 0
    with pytest.raises(RuntimeError, match="nope"):
        t.send("Page.enable")


def test_keepalive_renews(client, monkeypatch):
    monkeypatch.setattr(broker_client, "RENEW_EVERY", 0.1)
    with broker_client.lease("slow", ttl=2):
        (tid,) = list(client.reg.tabs)
        first = client.reg.tabs[tid]["expires"]
        assert client.wait_for(lambda: client.reg.tabs[tid]["expires"] > first + 0.05)


def test_status(client):
    with broker_client.lease("s"):
        s = broker_client.status()
        assert s["count"] == 1 and s["leases"][0]["owner"] == "s"


def test_show_and_stage(client, monkeypatch):
    monkeypatch.setenv("BROKER_OWNER", "env-owner")
    r = broker_client.show("https://a.example", dwell=30, need="matt-asked")
    assert r["owner"] == "env-owner"
    st = broker_client.stage()
    assert st["held_by"] == "env-owner"
    with pytest.raises(RuntimeError, match="screen busy: env-owner is showing https://a.example"):
        broker_client.show("https://b.example", owner="other", wait=1, need="matt-asked")


def test_failed_tab_connect_does_not_strand_the_lease(client, monkeypatch):
    # If the page socket cannot be opened, the lease must still be released and
    # the keepalive thread must stop renewing it.
    monkeypatch.setattr(broker_client, "RENEW_EVERY", 0.05)

    def boom(ws_url):
        raise ConnectionRefusedError("no page socket")
    monkeypatch.setattr(broker_client, "Tab", boom)
    with pytest.raises(ConnectionRefusedError):
        with broker_client.lease("unlucky"):
            pass
    assert client.wait_for(lambda: client.reg.tabs == {}), client.reg.tabs


# ---- keepalive behaviour against a scripted broker -------------------------

class Scripted(BaseHTTPRequestHandler):
    renew_codes = []
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        Scripted.seen.append((self.path, body))
        if self.path == "/lease":
            code, out = 200, {"lease_id": "L1", "ws_url": "ws://unused", "target_id": "T"}
        elif self.path == "/renew":
            code = Scripted.renew_codes.pop(0) if Scripted.renew_codes else 200
            out = {}
        else:
            code, out = 200, {}
        raw = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def scripted(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Scripted)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    Scripted.seen, Scripted.renew_codes = [], []
    monkeypatch.setattr(broker_client, "BROKER", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(broker_client, "RENEW_EVERY", 0.05)

    class FakeTab:
        def __init__(self, url):
            pass

        def close(self):
            pass
    monkeypatch.setattr(broker_client, "Tab", FakeTab)
    yield Scripted
    srv.shutdown()
    srv.server_close()


def renews():
    return [p for p, _ in Scripted.seen if p == "/renew"]


def test_keepalive_retries_after_a_failed_renew(scripted, monkeypatch):
    scripted.renew_codes = [500, 500]
    with broker_client.lease("x"):
        deadline = time.time() + 6
        while len(renews()) < 3 and time.time() < deadline:
            time.sleep(0.05)
    assert len(renews()) >= 3


def test_keepalive_stops_when_lease_is_gone(scripted):
    scripted.renew_codes = [404]
    with broker_client.lease("x"):
        time.sleep(0.5)
    assert len(renews()) == 1
    assert scripted.seen[-1][0] == "/release"


def test_release_failure_is_swallowed(scripted, monkeypatch):
    real = broker_client._post

    def post(path, payload):
        if path == "/release":
            raise OSError("broker went away")
        return real(path, payload)
    monkeypatch.setattr(broker_client, "_post", post)
    with broker_client.lease("x"):
        pass


def test_show_passes_through_non_409_errors(scripted, monkeypatch):
    from urllib.error import HTTPError

    def post(path, payload):
        raise HTTPError("u", 500, "boom", {}, None)
    monkeypatch.setattr(broker_client, "_post", post)
    with pytest.raises(HTTPError):
        broker_client.show("https://x")


def test_show_body(scripted):
    broker_client.show("https://x", owner="o", purpose="p", wait=3, dwell=4)
    assert scripted.seen[-1] == ("/show", {"owner": "o", "url": "https://x", "purpose": "p",
                                          "need": None, "wait": 3, "dwell": 4})


def _bare_tab():
    t = broker_client.Tab.__new__(broker_client.Tab)
    t.target_id, t.events = "T" * 32, []
    return t


def test_stuck_check_names_401_and_session_mismatch(monkeypatch):
    monkeypatch.setattr(broker_client, "_his_session", lambda url: "signed_in")
    after = {"url": "https://developer.apple.com/forums/", "text_len": 900, "signed_out": True, "signed_in": False}
    v = _bare_tab().stuck_check({"after": after, "new_tabs": [{"target": "x", "url": "about:blank"}],
                                 "http_errors": [{"status": 401, "url": "https://x/api"}]})
    assert any("401" in w for w in v["why"])
    assert any("blank" in w for w in v["why"])
    assert any("session mismatch" in w for w in v["why"])
    assert "--visible" in v["fix"]


def test_stuck_check_quiet_on_a_normal_page(monkeypatch):
    monkeypatch.setattr(broker_client, "_his_session", lambda url: "signed_in")
    after = {"url": "https://example.com/", "text_len": 900, "signed_out": False, "signed_in": True}
    assert _bare_tab().stuck_check({"after": after, "new_tabs": [], "http_errors": []}) is None


def test_click_falls_back_to_trusted_when_js_click_does_nothing(monkeypatch):
    t = _bare_tab()
    before = {"state": {"url": "u", "title": "a"}, "children": {}, "t": time.time()}
    monkeypatch.setattr(t, "_before", lambda shots, label: before)
    monkeypatch.setattr(t, "eval", lambda expr: True)
    monkeypatch.setattr(t, "_after", lambda *a: {"changed": [], "seconds": 3})
    monkeypatch.setattr(t, "click_trusted", lambda *a, **k: {"changed": ["page changed"], "how": "trusted"})
    assert t.click("Get started")["how"] == "js->trusted"


# ---- 2026-11-07: step timeouts, ask-the-human errors, one agent tab per site --------
class _SilentWS:
    """A page socket that never answers: what a hidden tab holding input looks like."""

    def __init__(self):
        self.t = None

    def send(self, raw):
        pass

    def settimeout(self, t):
        self.t = t

    def recv(self):
        import websocket
        time.sleep(min(self.t or 0, 0.2))
        raise websocket.WebSocketTimeoutException("timed out")


def test_send_gives_up_after_the_step_timeout():
    t = broker_client.Tab.__new__(broker_client.Tab)
    t.ws, t._id, t.step_timeout = _SilentWS(), 0, 0.2
    t0 = time.time()
    with pytest.raises(broker_client.StepTimeout, match="Input.dispatchMouseEvent"):
        t.send("Input.dispatchMouseEvent", type="mousePressed")
    assert time.time() - t0 < 2


def test_a_hung_click_raises_stuck_ask_human(monkeypatch):
    t = broker_client.Tab.__new__(broker_client.Tab)
    t.ws, t._id, t.step_timeout, t.owner = _SilentWS(), 0, 0.2, "x"
    t.events = __import__("collections").deque()
    with pytest.raises(broker_client.StuckAskHuman, match="Do not retry") as e:
        t.click_exact("More information...", shots=False)
    assert e.value.shot is None and e.value.labels == []


def test_human_guard_error_says_ask_dont_loop():
    t = broker_client.Tab.__new__(broker_client.Tab)

    class WS:
        def send(self, raw):
            self.mid = json.loads(raw)["id"]

        def recv(self):
            return json.dumps({"id": self.mid, "error": {
                "message": "the human is working in this tab right now (his last input was 3s ago)"}})
    t.ws, t._id = WS(), 0
    with pytest.raises(broker_client.HumanIsWorking, match="ask Matt in one line"):
        t.send("Input.dispatchMouseEvent")


def test_second_lease_on_a_site_reuses_the_agent_tab(client):
    with broker_client.lease("nd-restore-login", url="https://hpanel.example/login") as a:
        (tid,) = list(client.reg.tabs)
        with broker_client.lease("nd-restore", url="https://hpanel.example/backups") as b:
            assert list(client.reg.tabs) == [tid]           # no second tab
            assert b.target_id == tid and b.reused_tab_of == "nd-restore-login"
            assert client.reg.tabs[tid]["owner"] == "nd-restore"
        # the second job is done; the first still holds the tab, so it stays open
        assert tid in client.fake.targets and client.reg.tabs[tid]["owner"] == "nd-restore-login"
        assert a.eval("1") is not None
    assert client.wait_for(lambda: tid not in client.fake.targets)
    assert client.reg.tabs == {}


def test_shared_tab_closes_after_both_jobs_release(client):
    with broker_client.lease("one", url="https://site.example/a"):
        (tid,) = list(client.reg.tabs)
        with broker_client.lease("two", url="https://site.example/b"):
            pass
        assert tid in client.fake.targets
    assert client.wait_for(lambda: tid not in client.fake.targets)


def test_lease_on_another_site_gets_its_own_tab(client):
    with broker_client.lease("a", url="https://one.example/x"):
        with broker_client.lease("b", url="https://two.example/y"):
            assert len(client.reg.tabs) == 2
