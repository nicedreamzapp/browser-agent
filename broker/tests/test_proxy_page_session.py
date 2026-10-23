"""Page websockets (/devtools/page/<id>): the legacy /json path and REST leases."""
from conftest import http


def test_unknown_or_human_tab_is_refused(stack):
    human = stack.fake.add_human_tab()
    for tid in ("NOPE", human):
        c = stack.page_client(tid)
        assert c.close_code() == 1008
    assert not [c for c, _, _ in stack.fake.calls if c.startswith("page:")]


def test_pool_tab_round_trip_and_close_on_disconnect(stack):
    _, tabs = http("GET", stack.base + "/json")
    tid = tabs[0]["id"]
    c = stack.page_client(tid)
    r = c.call("Runtime.evaluate", expression="document.title")
    assert r["result"]["result"]["value"] == f"evaluated document.title in {tid}"
    assert stack.reg.tabs[tid]["attached"] == 1
    c.close()
    assert stack.wait_for(lambda: tid not in stack.reg.tabs and tid not in stack.fake.targets)


def test_pool_tab_belongs_to_one_page_socket(stack):
    _, t = http("PUT", stack.base + "/json/new")
    first = stack.page_client(t["id"])
    first.call("Page.enable")
    second = stack.page_client(t["id"])
    assert second.close_code() == 1008
    assert "result" in first.call("Page.enable")     # the first one is untouched


def test_focus_and_browser_commands_swallowed_on_page_socket(stack):
    _, t = http("PUT", stack.base + "/json/new")
    c = stack.page_client(t["id"])
    for method in ("Page.bringToFront", "Target.activateTarget", "Browser.close"):
        r = c.call(method, session_id="S9")
        assert r["result"] == {} and r["sessionId"] == "S9"
    assert stack.fake.methods(f"page:{t['id']}") == []


def test_rest_lease_outlives_its_page_socket(stack):
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "agent"})
    tid = lease["target_id"]
    assert lease["ws_url"] == f"ws://127.0.0.1:{stack.port}/devtools/page/{tid}"
    c = stack.page_client(tid)
    c.call("Page.enable")
    assert stack.reg.tabs[tid]["attached"] == 2
    c.close()
    assert stack.wait_for(lambda: stack.reg.tabs[tid]["attached"] == 1)
    assert tid in stack.fake.targets
    # and it can be reattached
    assert "result" in stack.page_client(tid).call("Page.enable")


def test_page_socket_reports_unreachable_browser(stack):
    _, t = http("PUT", stack.base + "/json/new")
    stack.proxy.UPSTREAM = 1       # nothing listens there
    c = stack.page_client(t["id"])
    assert c.close_code() == 1011
    assert stack.reg.tabs[t["id"]]["attached"] == 0   # the failed attach is not counted


def test_unknown_websocket_path_is_closed(stack):
    from conftest import CDPClient
    import websocket
    try:
        c = CDPClient(f"ws://127.0.0.1:{stack.port}/devtools/other/x")
    except websocket.WebSocketException:
        return                       # refused at the handshake: also fine
    stack.clients.append(c)
    assert c.close_code() == 1008
