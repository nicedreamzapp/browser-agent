"""The REST lease API on 9223 that broker_client.py talks to."""
import time

from conftest import http


def test_lease_renew_release(stack):
    code, l = http("POST", stack.api_base + "/lease",
                   {"owner": "yahoo-reaper", "url": "https://mail.example", "purpose": "sweep", "ttl": 120})
    assert code == 200
    tid = l["target_id"]
    assert l["expires_in"] == 120 and len(l["lease_id"]) == 12
    assert stack.fake.targets[tid]["url"] == "https://mail.example"
    info = stack.reg.tabs[tid]
    assert info["kind"] == "rest" and info["owner"] == "yahoo-reaper" and info["attached"] == 1

    before = info["expires"]
    code, r = http("POST", stack.api_base + "/renew", {"lease_id": l["lease_id"], "ttl": 600})
    assert code == 200 and r == {"lease_id": l["lease_id"], "expires_in": 600}
    assert info["expires"] > before + 400

    code, r = http("POST", stack.api_base + "/release", {"lease_id": l["lease_id"]})
    assert code == 200 and r == {"released": l["lease_id"], "tab_closed": True}
    assert tid not in stack.reg.tabs and tid not in stack.fake.targets


def test_lease_defaults(stack):
    code, l = http("POST", stack.api_base + "/lease", {})
    assert code == 200
    assert l["expires_in"] == stack.proxy.DEFAULT_TTL
    assert stack.reg.tabs[l["target_id"]]["owner"] == "anonymous"
    assert stack.fake.targets[l["target_id"]]["url"] == "about:blank"


def test_unknown_lease_is_404(stack):
    for path in ("/renew", "/release"):
        code, r = http("POST", stack.api_base + path, {"lease_id": "deadbeef0000"})
        assert code == 404 and r["error"] == "unknown lease"


def test_status_and_health(stack):
    _, l = http("POST", stack.api_base + "/lease", {"owner": "a", "ttl": 50})
    code, s = http("GET", stack.api_base + "/status")
    assert code == 200
    assert s["brave_alive"] is True and s["count"] == 1
    (lease,) = s["leases"]
    assert lease["lease_id"] == l["lease_id"] and lease["owner"] == "a"
    assert 45 < lease["expires_in"] <= 50
    assert http("GET", stack.api_base + "/health") == (200, {"ok": True, "brave": True})


def test_health_reports_a_dead_browser(stack):
    stack.fake.fail["Browser.getVersion"] = "gone"
    assert http("GET", stack.api_base + "/health") == (200, {"ok": True, "brave": False})


def test_adopt_is_gone_on_the_proxy(stack):
    human = stack.fake.add_human_tab()
    code, r = http("POST", stack.api_base + "/adopt", {"target_id": human})
    assert code == 410
    assert "Target.attachToTarget" not in stack.fake.methods()


def test_unknown_endpoints(stack):
    assert http("GET", stack.api_base + "/nope")[0] == 404
    assert http("POST", stack.api_base + "/nope", {})[0] == 404


def test_bad_json_is_a_500_not_a_crash(stack):
    import urllib.request, urllib.error
    req = urllib.request.Request(stack.api_base + "/lease", data=b"{nope", method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("expected an error")
    except urllib.error.HTTPError as e:
        assert e.code == 500
    assert http("GET", stack.api_base + "/health")[0] == 200


def test_browser_timeout_is_a_504(stack, monkeypatch):
    import concurrent.futures

    def slow(self, coro, timeout=30):
        coro.close()
        raise concurrent.futures.TimeoutError()
    monkeypatch.setattr(stack.proxy.ApiHandler, "_run", slow)
    code, r = http("POST", stack.api_base + "/lease", {})
    assert code == 504 and "did not answer" in r["error"]


def test_api_run_cancels_coroutine_on_timeout(stack):
    # A /show whose caller gave up must not put the page up later.
    import asyncio, concurrent.futures
    finished = []

    async def slow():
        await asyncio.sleep(0.5)
        finished.append(True)

    h = stack.proxy.ApiHandler.__new__(stack.proxy.ApiHandler)
    try:
        h._run(slow(), timeout=0.05)
        raise AssertionError("expected a timeout")
    except concurrent.futures.TimeoutError:
        pass
    time.sleep(0.7)
    assert finished == []


def test_release_without_lease_id_does_not_touch_other_clients_tabs(stack):
    # Pool and browser-client tabs carry lease_id None. A release or renew that
    # leaves lease_id out must not match them.
    c = stack.browser_client()
    ws_tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    _, pool = http("PUT", stack.base + "/json/new")
    for body in ({}, {"lease_id": None}):
        for path in ("/release", "/renew"):
            code, _ = http("POST", stack.api_base + path, body)
            assert code in (400, 404), (path, body, code)
    assert ws_tid in stack.reg.tabs and ws_tid in stack.fake.targets
    assert pool["id"] in stack.reg.tabs and pool["id"] in stack.fake.targets
