"""The DevTools HTTP surface on the proxy port (GET /json, PUT /json/new, ...)."""
import asyncio
import socket

from conftest import http


def test_json_version_points_tools_at_the_proxy(stack):
    code, v = http("GET", stack.base + "/json/version")
    assert code == 200
    assert v["Browser"] == "FakeChrome/1.0 (browser-broker)"
    assert v["webSocketDebuggerUrl"].startswith(f"ws://127.0.0.1:{stack.port}/devtools/browser/")
    assert v["Protocol-Version"] == "1.3"


def test_json_version_tolerates_trailing_slash(stack):
    # Playwright asks for /json/version/ with a trailing slash.
    code, v = http("GET", stack.base + "/json/version/")
    assert code == 200 and "webSocketDebuggerUrl" in v


def test_json_list_never_shows_the_humans_tabs(stack):
    human = stack.fake.add_human_tab()
    code, tabs = http("GET", stack.base + "/json")
    assert code == 200
    assert len(tabs) == 1
    assert tabs[0]["id"] != human
    assert tabs[0]["id"] in stack.reg.tabs
    assert stack.reg.tabs[tabs[0]["id"]]["kind"] == "pool"
    assert tabs[0]["webSocketDebuggerUrl"] == \
        f"ws://127.0.0.1:{stack.port}/devtools/page/{tabs[0]['id']}"


def test_json_list_alias(stack):
    code, tabs = http("GET", stack.base + "/json/list")
    assert code == 200 and len(tabs) == 1


def test_two_json_callers_get_different_pool_tabs(stack):
    # A listed pool tab is reserved for its caller for POOL_HOLD seconds.
    _, a = http("GET", stack.base + "/json")
    _, b = http("GET", stack.base + "/json")
    assert a[0]["id"] != b[0]["id"]


def test_unclaimed_pool_tab_is_reused_after_hold(stack):
    _, a = http("GET", stack.base + "/json")
    stack.reg.tabs[a[0]["id"]]["listed_until"] = 0     # hold elapsed, never attached
    _, b = http("GET", stack.base + "/json")
    assert a[0]["id"] == b[0]["id"]


def test_json_new_accepts_put_and_url(stack):
    code, t = http("PUT", stack.base + "/json/new?https://example.com/a%20b")
    assert code == 200
    assert stack.fake.targets[t["id"]]["url"] == "https://example.com/a b"
    assert stack.reg.tabs[t["id"]]["kind"] == "pool"


def test_json_new_get_still_works_and_defaults_to_blank(stack):
    code, t = http("GET", stack.base + "/json/new")
    assert code == 200
    assert stack.fake.targets[t["id"]]["url"] == "about:blank"


def test_json_new_opens_background_tab(stack):
    http("PUT", stack.base + "/json/new")
    (params,) = stack.fake.params_for("Target.createTarget")
    assert params["background"] is True


def test_json_activate_is_a_noop(stack):
    _, t = http("PUT", stack.base + "/json/new")
    code, body = http("GET", stack.base + f"/json/activate/{t['id']}")
    assert code == 200 and "noop" in body
    assert "Target.activateTarget" not in stack.fake.methods()


def test_json_close_closes_pool_tabs(stack):
    _, t = http("PUT", stack.base + "/json/new")
    code, _ = http("GET", stack.base + f"/json/close/{t['id']}")
    assert code == 200
    assert t["id"] not in stack.reg.tabs
    assert t["id"] not in stack.fake.targets


def test_json_close_refuses_human_and_leased_tabs(stack):
    human = stack.fake.add_human_tab()
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "a"})
    for tid in (human, lease["target_id"]):
        code, _ = http("GET", stack.base + f"/json/close/{tid}")
        assert code == 404
        assert tid in stack.fake.targets
    assert "Target.closeTarget" not in stack.fake.methods()


def test_json_protocol_is_passed_through(stack):
    code, p = http("GET", stack.base + "/json/protocol")
    assert code == 200 and p["version"]["major"] == "1"


def test_unknown_path_is_404(stack):
    code, _ = http("GET", stack.base + "/nope")
    assert code == 404


def test_upstream_error_is_a_500_not_a_crash(stack):
    stack.fake.fail["Target.createTarget"] = "boom"
    code, body = http("PUT", stack.base + "/json/new")
    assert code == 500 and "boom" in body
    # and the proxy still answers afterwards
    del stack.fake.fail["Target.createTarget"]
    assert http("PUT", stack.base + "/json/new")[0] == 200


def test_port_probe_and_garbage_are_closed_quietly(stack):
    for payload in (b"", b"HELLO\r\n\r\n"):
        s = socket.create_connection(("127.0.0.1", stack.port), timeout=12)
        s.sendall(payload)
        if not payload:
            s.shutdown(socket.SHUT_WR)
        assert s.recv(100) == b""
        s.close()
    assert http("GET", stack.base + "/json/version")[0] == 200


def test_inner_server_refuses_non_devtools_upgrade(stack):
    # process_request only lets /devtools/ through to the websocket handler.
    class Req:
        path = "/other"
    resp = asyncio.run(stack.proxy.process_request(None, Req()))
    assert resp.status_code == 404
    Req.path = "/devtools/page/x"
    assert asyncio.run(stack.proxy.process_request(None, Req())) is None
