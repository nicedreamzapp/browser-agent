"""Browser-level websocket clients (Playwright, Puppeteer, raw CDP): each one
sees only the tabs it created, and nothing it sends can reach the human's."""
import pytest

from conftest import http


def ids(reply):
    return {t["targetId"] for t in reply["result"]["targetInfos"]}


def test_get_targets_hides_the_humans_tabs_and_gives_a_first_page(stack):
    human = stack.fake.add_human_tab()
    c = stack.browser_client()
    got = ids(c.call("Target.getTargets"))
    assert len(got) == 1 and human not in got
    (tid,) = got
    assert stack.reg.tabs[tid]["kind"] == "ws"
    # asking again does not keep making tabs
    assert ids(c.call("Target.getTargets")) == got


def test_two_clients_are_blind_to_each_other(stack):
    a, b = stack.browser_client(), stack.browser_client()
    ta = a.call("Target.createTarget", url="https://a.example")["result"]["targetId"]
    tb = b.call("Target.createTarget", url="https://b.example")["result"]["targetId"]
    assert ids(a.call("Target.getTargets")) == {ta}
    assert ids(b.call("Target.getTargets")) == {tb}
    # cross-client attach is refused in both directions, as in issue #1
    for client, foreign in ((a, tb), (b, ta)):
        r = client.call("Target.attachToTarget", targetId=foreign, flatten=True)
        assert r["error"]["message"] == "No target with given id found"


@pytest.mark.parametrize("method", ["Target.attachToTarget", "Target.closeTarget",
                                    "Browser.getWindowForTarget"])
def test_commands_on_a_foreign_target_are_refused(stack, method):
    human = stack.fake.add_human_tab()
    c = stack.browser_client()
    r = c.call(method, targetId=human)
    assert "error" in r
    assert human in stack.fake.targets
    assert (method, human) not in [(m, p.get("targetId")) for _, m, p in stack.fake.calls]


def test_close_own_target(stack):
    c = stack.browser_client()
    tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    r = c.call("Target.closeTarget", targetId=tid)
    assert r["result"] == {"success": True}
    assert tid not in stack.reg.tabs and tid not in stack.fake.targets


def test_new_targets_are_background_tabs(stack):
    c = stack.browser_client()
    c.call("Target.createTarget", url="https://x.example")
    (p,) = stack.fake.params_for("Target.createTarget")
    assert p["background"] is True and p["url"] == "https://x.example"


@pytest.mark.parametrize("method", ["Target.activateTarget", "Page.bringToFront"])
def test_focus_stealers_are_answered_and_swallowed(stack, method):
    c = stack.browser_client()
    tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    r = c.call(method, targetId=tid)
    assert r["result"] == {}
    assert method not in stack.fake.methods()


@pytest.mark.parametrize("method", ["Browser.crash", "Browser.crashGpuProcess",
                                    "Browser.setWindowBounds"])
def test_browser_killers_are_swallowed(stack, method):
    c = stack.browser_client()
    assert c.call(method)["result"] == {}
    assert method not in stack.fake.methods()


def test_browser_close_never_reaches_the_browser_and_releases_the_client(stack):
    human = stack.fake.add_human_tab()
    c = stack.browser_client()
    tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    assert c.call("Browser.close")["result"] == {}
    assert c.close_code() == 1000          # the client's browser.close() resolves
    assert "Browser.close" not in stack.fake.methods()
    assert human in stack.fake.targets      # the human's browser is still there
    assert stack.wait_for(lambda: tid not in stack.fake.targets)


def test_browser_target_is_not_shared(stack):
    c = stack.browser_client()
    r = c.call("Target.attachToBrowserTarget")
    assert "not shared" in r["error"]["message"]
    assert "Target.attachToBrowserTarget" not in stack.fake.methods()


def test_disconnect_closes_every_tab_the_client_made(stack):
    human = stack.fake.add_human_tab()
    c = stack.browser_client()
    mine = {c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
            for _ in range(3)}
    c.close()
    assert stack.wait_for(lambda: not (mine & set(stack.fake.targets)))
    assert not (mine & set(stack.reg.tabs))
    assert human in stack.fake.targets


def test_discover_targets_only_reports_own_tabs(stack):
    human = stack.fake.add_human_tab()
    a, b = stack.browser_client(), stack.browser_client()
    a.call("Target.setDiscoverTargets", discover=True)
    tb = b.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    ta = a.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    a.drain()
    a.call("Target.closeTarget", targetId=ta)
    a.drain()
    seen = {(e["params"].get("targetInfo") or e["params"]).get("targetId")
            for e in a.events if e.get("method", "").startswith("Target.target")}
    assert seen == {ta}
    assert human not in seen and tb not in seen
    # and it hears that its own tab went away
    assert a.event_methods()[-1] == "Target.targetDestroyed"


def test_auto_attach_detaches_foreign_tabs_quietly(stack):
    human = stack.fake.add_human_tab()
    c = stack.browser_client()
    mine = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    c.call("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=False, flatten=True)
    c.drain()
    attached = [e["params"]["targetInfo"]["targetId"] for e in c.events
                if e.get("method") == "Target.attachedToTarget"]
    assert attached == [mine]
    # the proxy let go of the human's tab on the client's behalf
    assert stack.wait_for(lambda: any(
        conn.sessions and all(t != human for t in conn.sessions.values())
        for conn in stack.fake.conns))


def test_auto_attach_event_arrives_before_create_reply(stack):
    # Playwright and Puppeteer rely on this ordering.
    c = stack.browser_client()
    c.call("Target.setAutoAttach", autoAttach=True, waitForDebuggerOnStart=False, flatten=True)
    c.events.clear()
    tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    assert [e["params"]["targetInfo"]["targetId"] for e in c.events
            if e.get("method") == "Target.attachedToTarget"] == [tid]


def test_session_traffic_is_limited_to_own_sessions(stack):
    human = stack.fake.add_human_tab()
    c = stack.browser_client()
    tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    sid = c.call("Target.attachToTarget", targetId=tid, flatten=True)["result"]["sessionId"]
    r = c.call("Runtime.evaluate", session_id=sid, expression="1")
    assert r["sessionId"] == sid and r["result"] == {"echo": "Runtime.evaluate"}
    # Page.bringToFront inside a session is swallowed, sessionId kept on the reply
    r = c.call("Page.bringToFront", session_id=sid)
    assert r == {"id": r["id"], "result": {}, "sessionId": sid}
    assert "Page.bringToFront" not in stack.fake.methods()
    # detach through the proxy works for an own session
    assert "result" in c.call("Target.detachFromTarget", sessionId=sid)
    assert human in stack.fake.targets


def test_browser_contexts_are_per_client(stack):
    a, b = stack.browser_client(), stack.browser_client()
    ctx = a.call("Target.createBrowserContext")["result"]["browserContextId"]
    # b cannot create tabs in, or dispose, a's context
    r = b.call("Target.createTarget", url="about:blank", browserContextId=ctx)
    assert "Failed to find browser context" in r["error"]["message"]
    r = b.call("Target.disposeBrowserContext", browserContextId=ctx)
    assert "error" in r
    assert ctx in stack.fake.contexts
    tid = a.call("Target.createTarget", url="about:blank", browserContextId=ctx)["result"]["targetId"]
    assert stack.reg.tabs[tid]["context"] == ctx
    assert stack.fake.targets[tid]["browserContextId"] == ctx
    a.call("Target.disposeBrowserContext", browserContextId=ctx)
    assert ctx not in stack.fake.contexts
    assert tid not in stack.reg.tabs and tid not in stack.fake.targets


def test_contexts_are_disposed_when_client_leaves(stack):
    c = stack.browser_client()
    ctx = c.call("Target.createBrowserContext")["result"]["browserContextId"]
    c.close()
    assert stack.wait_for(lambda: ctx not in stack.fake.contexts)


def test_upstream_failure_answers_the_command_and_keeps_the_session(stack):
    c = stack.browser_client()
    tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    stack.fake.fail["Target.createTarget"] = "out of memory"
    r = c.call("Target.createTarget", url="about:blank")
    assert "browser-broker" in r["error"]["message"] and "out of memory" in r["error"]["message"]
    del stack.fake.fail["Target.createTarget"]
    # the session survived, and so did its tab
    assert ids(c.call("Target.getTargets")) == {tid}
    assert tid in stack.fake.targets


def test_non_json_frames_pass_through(stack):
    c = stack.browser_client()
    c.ws.send("not json")
    # the fake browser cannot parse it and drops the connection; the proxy must
    # not crash, and a fresh client still works
    c2 = stack.browser_client()
    assert "result" in c2.call("Target.getTargets")


def test_status_lists_browser_client_tabs(stack):
    c = stack.browser_client()
    tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    code, s = http("GET", stack.api_base + "/status")
    assert code == 200
    (row,) = s["tabs"]
    assert row["target"] == tid[:12] and row["kind"] == "ws" and row["expires_in"] is None
    assert s["leases"] == []   # browser clients are not REST leases


def test_quick_create_then_close_still_reports_created(stack):
    # A client that closes its new tab right after createTarget returns must
    # still hear targetCreated for it, then targetDestroyed, in that order.
    c = stack.browser_client()
    c.call("Target.setDiscoverTargets", discover=True)
    tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    c.call("Target.closeTarget", targetId=tid)
    c.drain(0.4)
    got = [(e["method"], (e["params"].get("targetInfo") or e["params"]).get("targetId"))
           for e in c.events if e.get("method", "").startswith("Target.target")]
    assert got == [("Target.targetCreated", tid), ("Target.targetDestroyed", tid)]
