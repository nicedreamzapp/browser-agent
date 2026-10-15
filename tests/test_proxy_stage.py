"""The stage: one agent at a time may put a page in front of the human."""
import asyncio
import sys
import time

import pytest

from conftest import http


def show(stack, **body):
    return http("POST", stack.api_base + "/show", body, timeout=30)


def test_show_opens_a_foreground_tab_and_activates_it_once(stack):
    code, r = show(stack, owner="page-audit", url="https://a.example", dwell=5)
    assert code == 200
    assert r["shown"] == "https://a.example" and r["owner"] == "page-audit" and r["held_for"] == 5
    (create,) = stack.fake.params_for("Target.createTarget")
    assert create["background"] is False
    assert stack.fake.params_for("Target.activateTarget") == [{"targetId": r["target_id"]}]
    code, st = http("GET", stack.api_base + "/stage")
    assert st["held_by"] == "page-audit" and st["showing"] == "https://a.example"
    assert 0 < st["seconds_left"] <= 5
    assert st["tab"] == r["target_id"][:12]


def test_show_needs_a_url(stack):
    code, r = show(stack, owner="x")
    assert code == 400 and "url" in r["error"]


def test_second_agent_gets_409_while_stage_is_held(stack):
    assert show(stack, owner="a", url="https://a.example", dwell=30)[0] == 200
    t = time.time()
    code, r = show(stack, owner="b", url="https://b.example", wait=1)
    assert code == 409
    assert time.time() - t >= 0.9                    # it queued for `wait` first
    assert r["held_by"] == "a" and r["showing"] == "https://a.example"
    assert r["seconds_left"] > 25


def test_second_agent_queues_and_gets_its_turn(stack):
    assert show(stack, owner="a", url="https://a.example", dwell=1)[0] == 200
    t = time.time()
    code, r = show(stack, owner="b", url="https://b.example", wait=10, dwell=1)
    assert code == 200 and r["owner"] == "b"
    assert 0.5 < time.time() - t < 5


def test_stage_frees_itself_after_dwell(stack):
    show(stack, owner="a", url="https://a.example", dwell=1)
    assert stack.wait_for(lambda: http("GET", stack.api_base + "/stage")[1]["held_by"] is None, 4)


def test_unstage_frees_it_now(stack):
    show(stack, owner="a", url="https://a.example", dwell=60)
    assert http("POST", stack.api_base + "/unstage", {}) == (200, {"freed": True})
    assert show(stack, owner="b", url="https://b.example", wait=1, dwell=1)[0] == 200
    assert http("POST", stack.api_base + "/unstage", {})[1]["freed"] is True
    assert http("POST", stack.api_base + "/unstage", {}) == (200, {"freed": False})


def test_reshow_closes_only_the_same_owners_previous_tab(stack):
    _, a1 = show(stack, owner="a", url="https://a.example/1", dwell=0)
    _, b1 = show(stack, owner="b", url="https://b.example/1", dwell=0)
    _, a2 = show(stack, owner="a", url="https://a.example/2", dwell=0)
    assert a1["target_id"] not in stack.fake.targets      # a's stale copy is gone
    assert b1["target_id"] in stack.fake.targets          # b's page is not a's to close
    assert a2["target_id"] in stack.fake.targets
    st = http("GET", stack.api_base + "/stage")[1]
    assert st["tabs"] == {"a": a2["target_id"][:12], "b": b1["target_id"][:12]}


def test_failed_show_does_not_leave_the_stage_locked(stack):
    stack.fake.fail["Target.createTarget"] = "bad url"
    code, r = show(stack, owner="a", url="chrome://nope", dwell=60)
    assert code == 500 and "bad url" in r["error"]
    del stack.fake.fail["Target.createTarget"]
    assert show(stack, owner="b", url="https://b.example", wait=1, dwell=1)[0] == 200


def test_show_with_wait_zero_on_a_free_stage(stack):
    # wait=0 means "don't queue": it should still show when nobody holds the stage.
    code, r = show(stack, owner="a", url="https://a.example", wait=0, dwell=30)
    assert code == 200, r
    code, r = show(stack, owner="b", url="https://b.example", wait=0)
    assert code == 409 and r["held_by"] == "a"


def test_stale_decay_does_not_free_a_later_holder(stack):
    st = stack.proxy.STAGE
    stack.run(st.show("a", "https://a.example", dwell=1))
    assert st.free() is True                         # /unstage a early
    stack.run(st.show("b", "https://b.example", dwell=30))
    time.sleep(1.3)                                  # a's decay timer fires now
    assert st.state()["held_by"] == "b"


def test_front_activates_the_app_on_macos(stack, monkeypatch):
    calls = []

    class P:
        async def wait(self):
            return 0

    async def fake_exec(*args, **kw):
        calls.append(args)
        return P()
    monkeypatch.setattr(stack.proxy.sys, "platform", "darwin")
    monkeypatch.setattr(stack.proxy.asyncio, "create_subprocess_exec", fake_exec)
    stack.run(stack.proxy.STAGE._front("T1"))
    assert calls == [("osascript", "-e", f'tell application "{stack.proxy.BROWSER_APP}" to activate')]


def test_front_skips_app_activation_elsewhere(stack, monkeypatch):
    called = []

    async def fake_exec(*args, **kw):
        called.append(args)
    monkeypatch.setattr(stack.proxy.sys, "platform", "linux")
    monkeypatch.setattr(stack.proxy.asyncio, "create_subprocess_exec", fake_exec)
    stack.run(stack.proxy.STAGE._front("T1"))
    assert called == []
    assert stack.fake.params_for("Target.activateTarget") == [{"targetId": "T1"}]


@pytest.mark.skipif(sys.platform != "darwin", reason="real osascript app activation is macOS only")
def test_front_real_osascript_on_macos(stack, monkeypatch):
    monkeypatch.setattr(stack.proxy, "BROWSER_APP", "Finder")
    stack.run(stack.proxy.STAGE._front("T1"))
