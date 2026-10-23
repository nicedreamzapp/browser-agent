"""The stage: one agent at a time may put a page in front of the human."""
import asyncio
import os
import sys
import time
import urllib.parse

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


def test_moving_to_another_site_closes_only_the_same_owners_previous_tab(stack):
    _, a1 = show(stack, owner="a", url="https://a.example/1", dwell=0)
    _, b1 = show(stack, owner="b", url="https://b.example/1", dwell=0)
    _, a2 = show(stack, owner="a", url="https://c.example/2", dwell=0)
    assert a1["target_id"] not in stack.fake.targets      # a's finished job is closed
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
    monkeypatch.setattr(stack.proxy, "STAGE_ACTIVATE_APP", True)
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


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("BB_TEST_REAL_FRONT") != "1",
                    reason="brings a real app forward; macOS only, opt in with BB_TEST_REAL_FRONT=1")
def test_front_real_osascript_on_macos(stack, monkeypatch):
    monkeypatch.setattr(stack.proxy, "STAGE_ACTIVATE_APP", True)
    monkeypatch.setattr(stack.proxy, "BROWSER_APP", "Finder")
    stack.run(stack.proxy.STAGE._front("T1"))


def unshow(stack, match):
    return http("POST", stack.api_base + "/unshow", {"match": match})


def test_unshow_closes_the_owners_stage_tab_and_frees_the_stage(stack):
    code, r = show(stack, owner="a", url="https://a.example/report", dwell=60)
    assert code == 200
    code, u = unshow(stack, "a")
    assert code == 200 and [t["owner"] for t in u["closed"]] == ["a"]
    assert r["target_id"] not in stack.fake.targets
    assert http("GET", stack.api_base + "/stage")[1]["held_by"] is None


def test_unshow_matches_a_url_substring_and_spares_other_owners(stack):
    _, ra = show(stack, owner="a", url="https://a.example/report", dwell=1)
    http("POST", stack.api_base + "/unstage", {})
    _, rb = show(stack, owner="b", url="https://b.example/other", dwell=1)
    code, u = unshow(stack, "a.example/rep")
    assert code == 200 and [t["owner"] for t in u["closed"]] == ["a"]
    assert ra["target_id"] not in stack.fake.targets
    assert rb["target_id"] in stack.fake.targets


def test_unshow_never_closes_a_human_tab_it_only_brought_forward(stack):
    human = stack.fake.add_human_tab(url="https://shop.example/order/1")
    code, r = show(stack, owner="a", url="https://shop.example/order/1", dwell=1)
    assert code == 200 and r["target_id"] == human
    code, u = unshow(stack, "a")
    assert code == 200 and u["closed"] == [] and u["left_open"][0]["owner"] == "a"
    assert human in stack.fake.targets


def test_unshow_needs_a_match(stack):
    assert http("POST", stack.api_base + "/unshow", {})[0] == 400


# ---------------------------------------------------------------------------
# Look before you touch (2026-10-23). A page already open is brought forward as
# it is, never reloaded unless the caller asks, and its state is handed back.
# These tests use the fake browser only: no real tab is created and nothing is
# brought to the front of a real screen.
# ---------------------------------------------------------------------------
def page_methods(stack, tid):
    return [m for c, m, _ in stack.fake.calls if c == f"page:{tid}"]


def test_show_of_an_open_page_does_not_reload_it(stack):
    human = stack.fake.add_human_tab(url="https://people.example/optout", title="Opt out")
    stack.fake.page_text[human] = "Your request has been submitted. Thank you."
    code, r = show(stack, owner="a", url="https://people.example/optout#form", dwell=0)
    assert code == 200, r
    assert r["target_id"] == human and r["already_open"] is True and r["reloaded"] is False
    assert "Page.navigate" not in page_methods(stack, human)
    assert "Page.reload" not in page_methods(stack, human)
    assert stack.fake.params_for("Target.createTarget") == []          # no duplicate copy
    assert r["page"]["url"] == "https://people.example/optout"
    assert r["page"]["title"] == "Opt out"
    assert r["page"]["text"].startswith("Your request has been submitted")


def test_show_twice_keeps_one_tab_and_never_reloads_it(stack):
    code, r1 = show(stack, owner="a", url="https://form.example/step1", dwell=0)
    assert code == 200 and r1["already_open"] is False
    code, r2 = show(stack, owner="a", url="https://form.example/step1", dwell=0)
    assert code == 200 and r2["target_id"] == r1["target_id"] and r2["already_open"] is True
    assert len(stack.fake.params_for("Target.createTarget")) == 1
    assert "Page.reload" not in page_methods(stack, r1["target_id"])
    assert "Page.navigate" not in page_methods(stack, r1["target_id"])
    # still the broker's own tab, so bb-close may close it
    code, u = unshow(stack, "a")
    assert [t["owner"] for t in u["closed"]] == ["a"]
    assert r1["target_id"] not in stack.fake.targets


def test_show_reloads_only_when_asked(stack):
    human = stack.fake.add_human_tab(url="https://news.example/live")
    code, r = show(stack, owner="a", url="https://news.example/live", dwell=0, reload=True)
    assert code == 200 and r["target_id"] == human
    assert r["already_open"] is True and r["reloaded"] is True and "page" not in r
    assert page_methods(stack, human) == ["Page.navigate", "Page.reload"]


def peek(stack, match, chars=None):
    q = urllib.parse.urlencode({"match": match, **({"chars": chars} if chars else {})})
    return http("GET", stack.api_base + "/peek?" + q)


def test_peek_reads_a_human_tab_without_touching_it(stack):
    human = stack.fake.add_human_tab(url="https://people.example/optout/done", title="Done")
    stack.fake.page_text[human] = "Success! " + "x" * 1000
    other = stack.fake.add_human_tab(url="https://elsewhere.example/")
    before = len(stack.fake.calls)
    code, r = peek(stack, "people.example")
    assert code == 200 and len(r["tabs"]) == 1
    (t,) = r["tabs"]
    assert t["target"] == human[:12] and t["title"] == "Done"
    assert t["url"] == "https://people.example/optout/done"
    assert t["text"].startswith("Success!") and len(t["text"]) == 400
    assert t["opened_by_broker"] is False and t["shown_by"] is None
    touched = {m for _, m, _ in stack.fake.calls[before:]}
    assert touched <= {"Target.getTargets", "Target.getTargetInfo", "Runtime.evaluate"}
    assert not any(c == f"page:{other}" for c, _, _ in stack.fake.calls)
    assert http("GET", stack.api_base + "/stage")[1]["held_by"] is None


def test_peek_by_owner_and_char_limit(stack):
    _, r = show(stack, owner="tps-agent", url="https://people.example/form", dwell=0)
    stack.fake.page_text[r["target_id"]] = "abcdefghij" * 10
    code, p = peek(stack, "tps-agent", chars=25)
    assert code == 200 and len(p["tabs"]) == 1
    assert p["tabs"][0]["shown_by"] == ["tps-agent"] and p["tabs"][0]["opened_by_broker"] is True
    assert p["tabs"][0]["text"] == ("abcdefghij" * 3)[:25]
    unshow(stack, "tps-agent")


def test_peek_no_match_and_missing_match(stack):
    assert peek(stack, "nothing-like-this") == (200, {"match": "nothing-like-this", "tabs": []})
    assert http("GET", stack.api_base + "/peek")[0] == 400
