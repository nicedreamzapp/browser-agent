"""The five rules of 2026-10-23, one section each, against the fake browser.

The night they were written, agents driving Matt's real browser opened a tab,
started a form, closed it, opened it again, "over 10 times right before my eyes",
and wiped his own typing and a solved captcha each time. Every test below pins
down one piece of the fix in code. None of them touches a real browser or brings
anything to the front of a real screen.
"""
import time
import urllib.parse

import pytest

from conftest import http


# ---- helpers ------------------------------------------------------------------
def show(stack, **body):
    body.setdefault("dwell", 0)
    return http("POST", stack.api_base + "/show", body, timeout=30)


def unshow(stack, match):
    return http("POST", stack.api_base + "/unshow", {"match": match})


def peek(stack, match, chars=None):
    q = urllib.parse.urlencode({"match": match, **({"chars": chars} if chars else {})})
    return http("GET", stack.api_base + "/peek?" + q)


def lease(stack, **body):
    return http("POST", stack.api_base + "/lease", body)


def release(stack, lease_id):
    return http("POST", stack.api_base + "/release", {"lease_id": lease_id})


def agent_methods(stack, tid):
    """What agents (and the stage) sent to a tab, without the broker's own watcher traffic."""
    return [m for c, m, _ in stack.fake.calls if c == f"page:{tid}"]


def watched(stack, tid):
    assert stack.wait_for(lambda: stack.fake.watchers.get(tid)), "the listener was never installed"


def human_types(stack, tid, kind="keydown", ago=0.0):
    """The person types in tab `tid` (`ago` seconds in the past)."""
    watched(stack, tid)
    t = stack.run(stack.fake.human(tid, kind, time.time() - ago))
    assert stack.wait_for(lambda: stack.proxy.HUMANS.last(tid) == t)
    return t


# ===============================================================================
# Rule 1. One tab per job.
# ===============================================================================
def test_rule1_same_site_twice_is_the_same_tab(stack):
    _, r1 = show(stack, owner="tps", url="https://people.example/optout")
    _, r2 = show(stack, owner="tps", url="https://people.example/optout")
    assert r2["target_id"] == r1["target_id"] and r2["already_open"] is True
    assert len(stack.fake.params_for("Target.createTarget")) == 1


def test_rule1_a_new_page_on_the_same_site_moves_the_job_tab_in_place(stack):
    _, r1 = show(stack, owner="invoice", url="https://shop.example/wp-admin/post.php?post=1")
    _, r2 = show(stack, owner="invoice", url="https://shop.example/wp-admin/post.php?post=2")
    assert r2["target_id"] == r1["target_id"] and r2["moved"] is True and r2["reloaded"] is False
    assert len(stack.fake.params_for("Target.createTarget")) == 1
    assert agent_methods(stack, r1["target_id"]) == ["Page.navigate"]          # no reload
    assert stack.fake.targets[r1["target_id"]]["url"].endswith("post=2")


def test_rule1_asking_again_for_the_page_the_human_moved_on_from_changes_nothing(stack):
    # He submitted the form, so the tab now shows the success page. The agent asks
    # for the form again: it gets the success page back, untouched.
    _, r1 = show(stack, owner="tps", url="https://people.example/optout")
    tid = r1["target_id"]
    stack.fake.targets[tid]["url"] = "https://people.example/optout/success"
    stack.fake.page_text[tid] = "Your opt-out request was received."
    _, r2 = show(stack, owner="tps", url="https://people.example/optout")
    assert r2["target_id"] == tid and r2["moved"] is False and r2["reloaded"] is False
    assert r2["page"]["url"].endswith("/optout/success")
    assert r2["page"]["text"].startswith("Your opt-out request")
    assert agent_methods(stack, tid) == ["Runtime.evaluate"]     # only the read-back


def test_rule1_a_lease_on_the_shown_jobs_site_gets_that_tab_not_a_hidden_copy(stack):
    _, r = show(stack, owner="tps", url="https://people.example/optout")
    code, l = lease(stack, owner="tps", url="https://people.example/optout")
    assert code == 200 and l["target_id"] == r["target_id"] and l["shared_with_shown_job"] is True
    assert len(stack.fake.params_for("Target.createTarget")) == 1
    code, rel = release(stack, l["lease_id"])
    assert rel["tab_closed"] is False and "bb-close" in rel["left_open"]
    assert r["target_id"] in stack.fake.targets                   # the job, not the lease, owns it
    _, u = unshow(stack, "tps")
    assert [c["owner"] for c in u["closed"]] == ["tps"] and r["target_id"] not in stack.fake.targets


def test_rule1_showing_brings_the_agents_own_hidden_tab_forward_instead_of_a_copy(stack):
    _, l = lease(stack, owner="tps", url="https://people.example/optout/step2")
    _, r = show(stack, owner="tps", url="https://people.example/optout")
    assert r["target_id"] == l["target_id"] and r["already_open"] is True
    assert len(stack.fake.params_for("Target.createTarget")) == 1
    _, u = unshow(stack, "tps")                                   # the broker made it, so it closes
    assert [c["owner"] for c in u["closed"]] == ["tps"] and l["target_id"] not in stack.fake.targets
    assert l["target_id"] not in stack.reg.tabs


def test_rule1_a_different_owner_is_a_different_job(stack):
    _, a = show(stack, owner="a", url="https://shop.example/x")
    _, b = show(stack, owner="b", url="https://shop.example/y")
    assert a["target_id"] != b["target_id"]


def test_rule1_a_job_tab_the_human_closed_is_simply_opened_again(stack):
    _, r1 = show(stack, owner="a", url="https://shop.example/x")
    stack.run(stack.fake._destroy(r1["target_id"]))
    code, r2 = show(stack, owner="a", url="https://shop.example/x")
    assert code == 200 and r2["target_id"] != r1["target_id"] and r2["already_open"] is False


# ===============================================================================
# Rule 2. Look before every step.
# ===============================================================================
def test_rule2_peek_reports_url_text_fields_and_human_state(stack):
    _, r = show(stack, owner="tps", url="https://people.example/optout")
    tid = r["target_id"]
    stack.fake.page_text[tid] = "Opt out of our records"
    stack.fake.page_fields[tid] = [{"field": "Email", "value": "matt@example.com"},
                                   {"field": "Password", "value": "(hidden)"}]
    code, p = peek(stack, "tps")
    assert code == 200
    (t,) = p["tabs"]
    assert t["url"] == "https://people.example/optout" and t["text"] == "Opt out of our records"
    assert t["fields"][0] == {"field": "Email", "value": "matt@example.com"}
    assert t["human"]["active"] is False and t["human_active_since"] is None
    assert t["shown_by"] == ["tps"] and t["opened_by_broker"] is True


def test_rule2_peek_by_lease_owner_and_by_target_id(stack):
    _, l = lease(stack, owner="agent-x", url="https://form.example/")
    assert [t["target"] for t in peek(stack, "agent-x")[1]["tabs"]] == [l["target_id"][:12]]
    (t,) = peek(stack, l["target_id"][:12])[1]["tabs"]
    assert t["leased_by"] == "agent-x"


def test_rule2_peek_changes_nothing(stack):
    human = stack.fake.add_human_tab(url="https://bank.example/")
    before = len(stack.fake.calls)
    peek(stack, "bank.example")
    sent = {m for _, m, _ in stack.fake.calls[before:]}
    assert sent <= {"Target.getTargets", "Target.getTargetInfo", "Runtime.evaluate"}
    assert not stack.fake.watchers.get(human)          # a peek installs nothing in his tab


def test_rule2_the_page_PEEK_JS_masks_secrets_and_skips_hidden_inputs(stack):
    js = stack.proxy.PEEK_JS
    assert "type === 'password'" in js and "(hidden)" in js
    assert "'hidden', 'submit'" in js and "card|cvv|cvc|ssn" in js


# ===============================================================================
# Rule 3. Notice when the human helps.
# ===============================================================================
def test_rule3_shown_and_leased_tabs_get_the_listener(stack):
    _, r = show(stack, owner="a", url="https://a.example/")
    _, l = lease(stack, owner="b", url="https://b.example/")
    for tid in (r["target_id"], l["target_id"]):
        watched(stack, tid)
        sent = stack.fake.methods(f"watch:{tid}")
        assert sent[:2] == ["Runtime.addBinding", "Runtime.enable"]
        assert "Page.addScriptToEvaluateOnNewDocument" in sent and "Runtime.evaluate" in sent
    js = stack.proxy.HUMAN_JS
    assert "isTrusted" in js and "__bbHumanEvent" in js and "IFRAME" in js


def test_rule3_his_typing_shows_up_in_peek_and_stage(stack):
    _, r = show(stack, owner="tps", url="https://people.example/optout")
    t = human_types(stack, r["target_id"])
    (p,) = peek(stack, "tps")[1]["tabs"]
    assert p["human"]["active"] is True and p["human"]["inputs"] == 1
    assert p["human_active_since"] == pytest.approx(t, abs=0.01)
    assert http("GET", stack.api_base + "/stage")[1]["jobs"]["tps"]["human"]["active"] is True


def test_rule3_the_agents_own_cdp_input_is_not_mistaken_for_him(stack):
    _, l = lease(stack, owner="a", url="https://a.example/")
    tid = l["target_id"]
    watched(stack, tid)
    c = stack.page_client(tid)
    assert "result" in c.call("Input.dispatchMouseEvent", type="mousePressed", x=1, y=1)
    stack.run(stack.fake.human(tid, "pointerdown"))                # the echo of that click
    time.sleep(0.2)
    assert stack.proxy.HUMANS.info(tid)["inputs"] == 0


def test_rule3_events_recorded_in_the_page_are_picked_up_by_a_peek(stack):
    # e.g. a broker restart: no binding pushed them, but the page kept its record
    _, r = show(stack, owner="a", url="https://a.example/")
    tid = r["target_id"]
    stack.fake.page_events[tid] = [[time.time() - 2, "input"]]
    (p,) = peek(stack, "a")[1]["tabs"]
    assert p["human"]["active"] is True and p["human"]["inputs"] == 1


def test_rule3_agent_input_waits_while_he_types_and_resumes_after(stack, monkeypatch):
    _, l = lease(stack, owner="a", url="https://a.example/")
    tid = l["target_id"]
    human_types(stack, tid)
    c = stack.page_client(tid)
    r = c.call("Input.insertText", text="hello")
    assert "the human is working in this tab" in r["error"]["message"]
    assert "Input.insertText" not in agent_methods(stack, tid)
    monkeypatch.setattr(stack.proxy, "HUMAN_QUIET", 0)             # he stopped
    assert "result" in c.call("Input.insertText", text="hello")


def test_rule3_show_hands_back_his_work_with_423_instead_of_reloading(stack):
    _, r = show(stack, owner="tps", url="https://people.example/optout")
    tid = r["target_id"]
    stack.fake.page_fields[tid] = [{"field": "Name", "value": "Matt"}]
    human_types(stack, tid)
    code, e = show(stack, owner="tps", url="https://people.example/optout", reload=True)
    assert code == 423 and e["human_active"] is True
    assert e["page"]["fields"] == [{"field": "Name", "value": "Matt"}]
    code, e = show(stack, owner="tps", url="https://people.example/other-page")
    assert code == 423
    assert "Page.reload" not in agent_methods(stack, tid) and "Page.navigate" not in agent_methods(stack, tid)
    assert http("GET", stack.api_base + "/stage")[1]["held_by"] is None     # a refusal frees the screen
    code, again = show(stack, owner="tps", url="https://people.example/optout")
    assert code == 200 and again["target_id"] == tid and again["page"]["human"]["active"] is True


# ===============================================================================
# Rule 4. No reloading or closing mid-job.
# ===============================================================================
def test_rule4_a_shown_job_tab_cannot_be_reloaded_through_the_proxy(stack):
    _, r = show(stack, owner="tps", url="https://people.example/optout")
    _, l = lease(stack, owner="tps", url="https://people.example/optout")
    c = stack.page_client(l["target_id"])
    for method, params in (("Page.reload", {}), ("Page.navigate", {"url": "https://people.example/optout#x"})):
        m = c.call(method, **params)
        assert "not done" in m["error"]["message"] and "bb-close tps" in m["error"]["message"]
    assert "result" in c.call("Page.navigate", url="https://people.example/optout/next")   # moving on is fine
    assert agent_methods(stack, r["target_id"]) == ["Page.navigate"]


def test_rule4_a_plain_leased_tab_can_still_reload_when_nobody_is_in_it(stack):
    _, l = lease(stack, owner="a", url="https://a.example/")
    c = stack.page_client(l["target_id"])
    assert "result" in c.call("Page.reload")


def test_rule4_nothing_reloads_navigates_or_closes_a_tab_he_is_typing_in(stack):
    b = stack.browser_client()
    tid = b.call("Target.createTarget", url="https://a.example/")["result"]["targetId"]
    sid = b.call("Target.attachToTarget", targetId=tid, flatten=True)["result"]["sessionId"]
    human_types(stack, tid)
    for method, params in (("Page.reload", {}), ("Page.navigate", {"url": "https://elsewhere.example/"}),
                           ("Input.dispatchKeyEvent", {"type": "keyDown"})):
        m = b.call(method, session_id=sid, **params)
        assert "the human is working in this tab" in m["error"]["message"], method
    m = b.call("Target.closeTarget", targetId=tid)
    assert "the human is working in this tab" in m["error"]["message"]
    assert tid in stack.fake.targets


def test_rule4_bb_close_leaves_a_tab_he_is_working_in_open_and_hands_it_over(stack):
    _, r = show(stack, owner="tps", url="https://people.example/optout")
    tid = r["target_id"]
    human_types(stack, tid)
    code, u = unshow(stack, "tps")
    assert code == 200 and u["closed"] == [] and "working in it" in u["left_open"][0]["why"]
    assert tid in stack.fake.targets and tid not in stack.proxy.STAGE.created
    assert "tps" not in stack.proxy.STAGE.jobs


def test_rule4_releasing_a_lease_he_is_working_in_leaves_it_open(stack):
    _, l = lease(stack, owner="a", url="https://a.example/")
    human_types(stack, l["target_id"])
    code, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is False and r["left_open"] == "the human is working in it"
    assert l["target_id"] in stack.fake.targets and l["target_id"] not in stack.reg.tabs


def test_rule4_lease_expiry_never_closes_a_tab_he_is_working_in(stack):
    _, l = lease(stack, owner="a", url="https://a.example/", ttl=60)
    human_types(stack, l["target_id"])
    stack.reg.tabs[l["target_id"]]["expires"] = time.time() - 1
    stack.run(stack.reg.reap())
    assert l["target_id"] in stack.fake.targets and l["target_id"] not in stack.reg.tabs


def test_rule4_moving_to_another_site_never_closes_the_tab_he_is_in(stack):
    _, r1 = show(stack, owner="a", url="https://one.example/")
    human_types(stack, r1["target_id"])
    code, r2 = show(stack, owner="a", url="https://two.example/")
    assert code == 200 and r2["target_id"] != r1["target_id"]
    assert r1["target_id"] in stack.fake.targets


def test_rule4_a_restarted_broker_leaves_tabs_he_worked_in_open(stack):
    _, a = lease(stack, owner="a", url="https://a.example/")
    _, b = lease(stack, owner="b", url="https://b.example/")
    stack.fake.page_events[a["target_id"]] = [[time.time() - 3600, "keydown"]]
    stack.reg.tabs.clear()                     # the old process is gone; tabs.json remains
    stack.run(stack.reg.close_leftovers())
    assert a["target_id"] in stack.fake.targets and b["target_id"] not in stack.fake.targets


def test_rule4_reload_is_only_ever_on_request(stack):
    _, r = show(stack, owner="a", url="https://a.example/p")
    for _ in range(3):
        show(stack, owner="a", url="https://a.example/p")
    assert "Page.reload" not in agent_methods(stack, r["target_id"])


# ===============================================================================
# Rule 5. Stuck means ask, not retry.
# ===============================================================================
def test_rule5_open_close_open_close_then_the_third_open_is_refused(stack):
    url = "https://people.example/optout"
    for _ in range(2):
        assert show(stack, owner="tps", url=url)[0] == 200
        assert unshow(stack, "tps")[0] == 200
    code, e = show(stack, owner="tps", url=url)
    assert code == 429 and e["stop_and_ask"] is True
    assert "Ask the human in one line" in e["error"] and "bb-peek tps" in e["error"]
    assert len(stack.fake.params_for("Target.createTarget")) == 2
    assert http("GET", stack.api_base + "/stage")[1]["held_by"] is None


def test_rule5_repeated_reloads_count_too(stack):
    url = "https://a.example/form"
    show(stack, owner="a", url=url)
    assert show(stack, owner="a", url=url, reload=True)[0] == 200
    assert show(stack, owner="a", url=url, reload=True)[0] == 200
    assert show(stack, owner="a", url=url, reload=True)[0] == 429


def test_rule5_other_pages_and_other_jobs_are_not_affected(stack):
    url = "https://people.example/optout"
    for _ in range(2):
        show(stack, owner="tps", url=url)
        unshow(stack, "tps")
    assert show(stack, owner="tps", url="https://shop.example/wp-admin/post.php?post=9")[0] == 200
    assert show(stack, owner="someone-else", url=url)[0] == 200


def test_rule5_the_count_forgets_after_the_window(stack, monkeypatch):
    url = "https://people.example/optout"
    for _ in range(2):
        show(stack, owner="tps", url=url)
        unshow(stack, "tps")
    monkeypatch.setattr(stack.proxy, "CHURN_WINDOW", 0)
    assert show(stack, owner="tps", url=url)[0] == 200


def test_rule5_python_client_raises_the_right_errors(stack, monkeypatch):
    import broker_client
    monkeypatch.setattr(broker_client, "BROKER", stack.api_base)
    url = "https://people.example/optout"
    r = broker_client.show(url, owner="tps", dwell=0)
    human_types(stack, r["target_id"])
    with pytest.raises(broker_client.HumanIsWorking) as e:
        broker_client.show(url, owner="tps", dwell=0, reload=True)
    assert e.value.page["human"]["active"] is True
    assert broker_client.human_active("tps") is True
    monkeypatch.setattr(stack.proxy, "HUMAN_QUIET", 0)
    broker_client.unshow("tps")
    broker_client.show(url, owner="tps", dwell=0)
    broker_client.unshow("tps")
    with pytest.raises(broker_client.StopAndAsk):
        broker_client.show(url, owner="tps", dwell=0)


# ===============================================================================
# The bookkeeping underneath
# ===============================================================================
def test_humans_stretch_echo_and_dedupe(monkeypatch):
    import proxy
    h = proxy.Humans()
    now = time.time()
    assert h.info("T", now)["active"] is False and h.since("T") is None
    h.note("T", now - 50, "keydown")
    h.note("T", now - 10, "keydown")
    h.note("T", now - 5, "input")
    assert h.note("T", now - 5, "input") is False                  # same event twice
    assert h.since("T") == now - 10                                # the gap before was > HUMAN_QUIET
    assert h.active("T", now) and not h.active("T", now + 30)
    h.agent_input("T", now)
    assert h.note("T", now + 0.5, "pointerdown") is False          # the agent's own click
    assert h.note("T", now + 3, "pointerdown") is True
    h.merge("T", [[now + 4, "paste"], ["bad"], None])
    assert h.last("T") == now + 4
    with pytest.raises(proxy.HumanBusy) as e:
        h.guard("T", "reloaded")
    assert "not reloaded" in str(e.value)


def test_page_key_and_site(stack):
    p = stack.proxy
    assert p.page_key("https://a.example/x/#top") == "https://a.example/x"
    assert p.site_of("https://www.Shop.example/a") == "shop.example"
    assert p.site_of("about:blank") == "about:blank"


def test_file_upload_is_the_agents_own_input(monkeypatch):
    import asyncio, proxy
    monkeypatch.setattr(proxy, "HUMANS", proxy.Humans())
    assert asyncio.run(proxy.agent_gate("T", "DOM.setFileInputFiles", {})) is None
    now = time.time()
    assert proxy.HUMANS.note("T", now + 0.3, "change") is False   # the upload's own change event
    assert proxy.HUMANS.note("T", now + 0.4, "input") is False


# ===============================================================================
# 2026-10-25: the agent's scripted typing is not him, and a tab he touched is his
# ===============================================================================
def test_agent_script_typing_echo_is_not_mistaken_for_him(stack):
    # An agent types with Runtime.evaluate (focus + execCommand insertText) and clicks
    # send with a dispatched event. Chrome fires trusted input/change for that.
    _, l = lease(stack, owner="ask", url="https://chat.example/")
    tid = l["target_id"]
    watched(stack, tid)
    c = stack.page_client(tid)
    assert "result" in c.call("DOM.focus", nodeId=1)
    assert "result" in c.call("Runtime.evaluate",
                              expression="document.execCommand('insertText', false, 'hi')")
    assert "result" in c.call("Runtime.callFunctionOn", functionDeclaration="function(){}")
    for kind in ("input", "change"):
        stack.run(stack.fake.human(tid, kind))
    time.sleep(0.2)
    assert stack.proxy.HUMANS.info(tid)["inputs"] == 0
    code, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is True and tid not in stack.fake.targets


def test_a_late_change_after_agent_typing_with_no_key_or_click_is_the_agents(monkeypatch):
    import proxy
    h = proxy.Humans()
    now = time.time()
    h.agent_input("T", now)                                        # Input.insertText
    assert h.note("T", now + 8, "change") is False                 # blur long after: still the agent's
    h.agent_touch("T", now + 10)                                   # a Runtime.evaluate
    assert h.note("T", now + 25, "input") is False
    assert h.note("T", now + 10 + proxy.AGENT_ACTIVE + 5, "input") is True   # agent long gone: him (dictation)


def test_his_real_click_still_counts_mid_agent_work(monkeypatch):
    import proxy
    h = proxy.Humans()
    now = time.time()
    h.agent_touch("T", now)
    assert h.note("T", now + 0.1, "pointerdown") is True           # scripts never mask a real click
    assert h.note("T", now + 0.6, "keydown") is True
    assert h.note("T", now + 0.7, "input") is True                 # he typed after the agent: his input counts
    assert h.note("T", now + 3, "change") is True


def test_releasing_27s_after_his_click_leaves_the_tab_open(stack):
    _, l = lease(stack, owner="cc", url="https://a.example/")
    tid = l["target_id"]
    stack.reg.tabs[tid]["created"] -= 60                           # the lease began a minute ago
    human_types(stack, tid, kind="pointerdown", ago=27)            # he clicked 27s ago
    assert stack.proxy.HUMANS.active(tid) is False                 # past HUMAN_QUIET
    code, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is False and tid in stack.fake.targets and tid not in stack.reg.tabs


def test_lease_expiry_after_his_click_leaves_the_tab_open(stack):
    _, l = lease(stack, owner="cc", url="https://a.example/", ttl=60)
    tid = l["target_id"]
    stack.reg.tabs[tid]["created"] -= 60                           # the lease began a minute ago
    human_types(stack, tid, kind="pointerdown", ago=40)            # he clicked 40s ago
    stack.reg.tabs[tid]["expires"] = time.time() - 1
    stack.run(stack.reg.reap())
    assert tid in stack.fake.targets and tid not in stack.reg.tabs


def test_a_hidden_lease_on_screen_with_no_input_from_him_still_closes(stack):
    # 2026-10-29: being on screen is not him. The tab in front closed, or the page focused itself.
    _, l = lease(stack, owner="cc", url="https://a.example/")      # hidden by default
    tid = l["target_id"]
    stack.fake.visible.add(tid)
    code, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is True and tid not in stack.fake.targets


def test_a_hidden_lease_he_switched_to_and_scrolled_is_left_open(stack):
    _, l = lease(stack, owner="cc", url="https://a.example/")
    tid = l["target_id"]
    stack.fake.visible.add(tid)
    stack.reg.tabs[tid]["created"] -= 60                           # the lease began a minute ago
    human_types(stack, tid, kind="wheel", ago=40)                  # reading it, quiet for 40s now
    code, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is False and tid in stack.fake.targets


def test_linkedin_post_from_a_hidden_lease_closes_on_release(stack):
    # 2026-10-29, the mini: insertText, an image upload, a click on Post, all by the agent,
    # each echoed by the page as trusted events, and the tab came on screen. Nobody touched it.
    _, l = lease(stack, owner="linkedin-kit-post", url="https://www.linkedin.com/feed/?shareActive=true")
    tid = l["target_id"]
    c = stack.page_client(tid)
    watched(stack, tid)
    for method, params, echoes in [
            ("Input.insertText", {"text": "In August someone asked"}, ["keydown", "input"]),
            ("DOM.setFileInputFiles", {"files": ["/tmp/hero.jpg"], "objectId": "1"}, ["input", "change"]),
            ("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 5, "y": 5, "button": "left",
                                          "clickCount": 1}, ["pointerdown"])]:
        assert "error" not in c.call(method, **params)
        for k in echoes:
            stack.run(stack.fake.human(tid, k))                    # the page's own trusted echo
            time.sleep(0.3)
    stack.fake.visible.add(tid)                                    # it came on screen by itself
    time.sleep(0.2)
    assert stack.proxy.HUMANS.touched_since(tid, 0) is False
    code, r = release(stack, l["lease_id"])
    assert code == 200 and r["tab_closed"] is True and tid not in stack.fake.targets


def test_his_real_click_during_a_lease_keeps_the_tab(stack):
    _, l = lease(stack, owner="linkedin-kit-post", url="https://www.linkedin.com/feed/")
    tid = l["target_id"]
    c = stack.page_client(tid)
    assert "error" not in c.call("Input.insertText", text="draft")
    time.sleep(2)                                                  # well past the agent's echo window
    human_types(stack, tid, kind="pointerdown", ago=0)             # he clicks in it
    code, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is False and tid in stack.fake.targets


def test_a_hidden_lease_off_screen_with_no_input_still_closes(stack):
    _, l = lease(stack, owner="cc", url="https://a.example/")
    code, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is True and l["target_id"] not in stack.fake.targets


def test_a_visible_by_design_lease_with_no_input_still_closes(stack):
    _, l = lease(stack, owner="cc", url="https://a.example/", hidden=False)
    tid = l["target_id"]
    stack.fake.visible.add(tid)
    code, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is True and tid not in stack.fake.targets


def test_long_polling_job_does_not_turn_its_own_typing_into_his(monkeypatch):
    # 2026-10-25: a Gemini job typed its question by script, then polled ~200 times for the answer.
    # At release the page's record of that typing was re-judged after the old touch cap dropped the
    # typing script, so the tab was left open "for the human". Three tabs piled up on his screen.
    import proxy
    h = proxy.Humans()
    now = time.time()
    h.agent_touch("T", now)                                        # the script that typed the question
    assert h.note("T", now + 0.01, "input") is False               # pushed live: the agent's
    for i in range(500):                                           # minutes of polling for the answer
        h.agent_touch("T", now + 4 + i * 0.8)
    h.merge("T", [[now + 0.01, "input"]])                          # release reads the page's own record
    assert h.touched_since("T", now - 1) is False
    h2 = proxy.Humans()                                            # even with no live push, merge alone
    h2.agent_touch("T", now)
    for i in range(500):
        h2.agent_touch("T", now + 4 + i * 0.8)
    h2.merge("T", [[now + 0.01, "input"]])
    assert h2.touched_since("T", now - 1) is False
