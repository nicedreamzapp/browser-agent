"""The five rules of 2026-10-23, as the agent's step loop obeys them.

The night they were written, agents fought Matt over one browser tab: open a form, start
filling it in, close it, open it again, "over 10 times right before my eyes", wiping his own
typing and a solved captcha. Each test pins down one piece of the fix. No model, browser,
broker or phone is involved: see conftest.py.
"""
import asyncio
import time

import pytest

import agent
from conftest import FakeTab, run

FORM = "https://people.example/optout"
nav = lambda url: {"tool": "navigate", "args": {"url": url}}
click = lambda uid: {"tool": "click", "args": {"uid": uid}}
typ = lambda uid, text: {"tool": "type_text", "args": {"uid": uid, "text": text}}
done = {"tool": "done", "args": {"message": "finished"}}


# ===============================================================================
# Rule 1. One tab per job.
# ===============================================================================
def test_rule1_a_task_that_asks_keeps_its_tab_for_the_answer(monkeypatch):
    released = []
    monkeypatch.setattr(agent, "_release_tab", lambda: released.append(1))

    class Conn(FakeTab):
        async def connect(self):
            pass
    monkeypatch.setattr(agent, "CDP", Conn)
    monkeypatch.setattr(agent, "ask_model", lambda m: '{"tool": "ask", "args": {"question": "Which email?"}}')
    out = asyncio.run(agent.run("fill the form", keep_tab=True))
    assert out["asked"] == "Which email?" and released == []           # the tab waits for the answer
    asyncio.run(agent.run("fill the form", keep_tab=False))
    assert released == [1]                                             # one-shot runs always close it


def test_rule1_finishing_a_task_always_closes_its_tab(monkeypatch):
    released = []
    monkeypatch.setattr(agent, "_release_tab", lambda: released.append(1))

    class Conn(FakeTab):
        async def connect(self):
            pass
    monkeypatch.setattr(agent, "CDP", Conn)
    monkeypatch.setattr(agent, "ask_model", lambda m: '{"tool": "done", "args": {"message": "ok"}}')
    assert asyncio.run(agent.run("x", keep_tab=True)) is None and released == [1]


def test_rule1_the_answer_continues_the_same_job():
    t = agent.resume_task({"task": "opt me out", "asked": "Which email?"}, "matt@example.com")
    assert t.startswith("opt me out") and "Which email?" in t and "matt@example.com" in t
    assert "SAME tab" in t and "do not start over" in t


# ===============================================================================
# Rule 2. Look before every step.
# ===============================================================================
def test_rule2_looks_before_every_model_turn_and_every_action(tab, model):
    model(nav(FORM), [typ(1, "matt@example.com"), click(2)], done)
    run("fill the opt-out form", tab)
    log = [e if isinstance(e, str) else e[0] for e in tab.log]
    for i, e in enumerate(log):
        if e in ("type", "click"):
            assert log[i - 1] == "look", f"{e} at {i} was not preceded by a look: {log}"


# ===============================================================================
# Rule 3. Notice when the human helps, and continue with him.
# ===============================================================================
def test_rule3_his_typing_between_steps_is_handed_to_the_model(tab, model):
    m = model(nav(FORM), done)
    tab.script[3] = lambda t: t.human(Email="matt@example.com", Captcha="solved")
    run("fill the opt-out form", tab)
    prompt = m.last_user()
    assert "THE HUMAN WORKED IN THIS TAB" in prompt
    assert '"Email" now holds "matt@example.com"' in prompt and '"Captcha" now holds "solved"' in prompt
    assert "do not go back to the start" in prompt and "do not reload" in prompt


def test_rule3_after_he_submits_the_agent_never_reopens_the_form_on_top(tab, model):
    m = model(nav(FORM), nav(FORM), done)
    def he_submits(t):
        t.url = FORM + "/success"
        t.human_last = time.time() - 30
    tab.script[3] = he_submits
    out = run("fill the opt-out form", tab)
    assert f"the page moved from {FORM} to {FORM}/success" in m.prompts[1][-1]["content"]
    assert tab.actions().count(("navigate", FORM)) == 1 and tab.url == FORM + "/success"
    assert "You already moved this page on" in out["asked"]


def test_rule3_a_batch_stops_the_moment_he_acts(tab, model):
    m = model(nav(FORM), [typ(1, "agent text"), click(2)], done)
    # looks: 1-2 after the navigate, 3 before step 2, 4 before the type, 5 after it, 6 before the click
    tab.script[6] = lambda t: t.human(Email="his text")
    run("fill the opt-out form", tab)
    assert ("click", 2) not in tab.actions()
    assert "stopped before click: the human changed the page" in m.prompts[2][-1]["content"]


def test_rule3_the_agents_own_typing_is_not_reported_as_his(tab, model):
    m = model(nav(FORM), typ(1, "matt@example.com"), done)
    run("fill the opt-out form", tab)
    assert not any("THE HUMAN WORKED" in str(p[-1]["content"]) for p in m.prompts)


def test_rule3_a_redirect_after_our_click_is_not_blamed_on_him(tab, model):
    m = model(nav(FORM), done)
    tab.script[3] = lambda t: setattr(t, "url", FORM + "/redirected")   # no input from him
    run("fill the opt-out form", tab)
    assert "THE HUMAN WORKED" not in m.last_user()


def test_rule3_pauses_while_he_is_working(tab, model, monkeypatch):
    slept = []
    async def fake_sleep(s):
        slept.append(s)
        if len(slept) == 3:
            tab.human_last = time.time() - 60                  # he stopped a while ago
    monkeypatch.setattr(agent.asyncio, "sleep", fake_sleep)
    m = model(nav(FORM), done)
    tab.script[3] = lambda t: t.human(ago=0, Email="typing...")
    run("fill the opt-out form", tab)
    assert slept == [2, 2, 2]
    assert len(m.prompts) == 2 and "THE HUMAN WORKED" in m.last_user()


def test_rule3_asks_when_he_stays_busy(tab, model, monkeypatch, quiet):
    async def fake_sleep(s):
        tab.human_last = time.time()
    monkeypatch.setattr(agent.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(agent, "HUMAN_WAIT", 6)
    model(nav(FORM), done)
    tab.script[3] = lambda t: t.human(ago=0, Email="x")
    out = run("fill the opt-out form", tab)
    assert "You're working in the tab" in out["asked"] and out["asked"].endswith("?")
    assert quiet and quiet[-1].startswith("❓")


def test_rule3_a_broker_refusal_means_wait_for_him_not_failure(tab, model):
    m = model(nav(FORM), click(2), done)
    tab.refuse_next = "browser-broker: the human is working in this tab right now (his last input was 2s ago)"
    out = run("submit the form", tab)
    assert out is None
    assert "PAUSED: the human is working in this tab" in m.prompts[2][-1]["content"]
    assert "That failed" not in m.prompts[2][-1]["content"]


# ===============================================================================
# Rule 4. No reloading or reopening mid-job.
# ===============================================================================
def test_rule4_navigating_to_the_page_it_is_on_is_never_a_reload(tab, model):
    m = model(nav(FORM), nav(FORM + "#top"), done)
    run("open the form", tab)
    assert tab.actions().count(("navigate", FORM)) == 1
    assert "It was NOT reloaded" in m.prompts[2][-1]["content"]


def test_rule4_a_mid_job_refusal_from_the_broker_is_reported(tab, model):
    m = model(nav(FORM), click(2), done)
    tab.refuse_next = "browser-broker: this tab is on screen for the job \"x\" and that job is not done"
    run("submit", tab)
    assert "REFUSED by browser-broker" in m.prompts[2][-1]["content"]


# ===============================================================================
# Rule 5. Stuck means ask, not retry.
# ===============================================================================
def test_rule5_one_failure_gets_one_different_try(tab, model):
    tab.fail_clicks = {"9"}
    m = model(nav(FORM), click(9), click(2), done)
    assert run("submit", tab) is None
    assert "Try ONE clearly different approach" in m.prompts[2][-1]["content"]
    assert "call ask(question)" in m.prompts[2][-1]["content"]


def test_rule5_two_failures_in_a_row_stop_and_ask_in_one_line(tab, model):
    tab.fail_clicks = {"9", "8"}
    m = model(nav(FORM), click(9), click(8), click(2), done)
    out = run("submit", tab)
    assert out["asked"].startswith(f"I'm stuck on {FORM}") and out["asked"].endswith("?")
    assert "\n" not in out["asked"]
    assert ("click", 2) not in tab.actions() and len(m.prompts) == 3


def test_rule5_reopening_a_page_after_a_failure_asks_instead(tab, model):
    tab.fail_clicks = {"9"}
    model(nav(FORM), nav("https://people.example/other"), click(9), nav(FORM), done)
    out = run("submit", tab)
    assert out and "Reopening it would start over" in out["asked"]
    assert tab.actions().count(("navigate", FORM)) == 1


def test_rule5_clicking_the_same_thing_three_times_asks(tab, model):
    model(nav(FORM), click(2), click(2), click(2), done)
    out = run("submit", tab)
    assert "clicked the same thing 3 times" in out["asked"]
    assert tab.actions().count(("click", 2)) == 2


def test_rule5_the_model_can_ask_itself(tab, model):
    model(nav(FORM), {"tool": "ask", "args": {"question": "Use your work or home email?"}})
    out = run("fill the form", tab)
    assert out["asked"] == "Use your work or home email?" and out["state"]["url"] == FORM


def test_rule5_running_out_of_steps_asks(tab, model, monkeypatch):
    monkeypatch.setattr(agent, "MAX_STEPS", 2)
    model(nav(FORM), {"tool": "scroll", "args": {"direction": "down"}})
    assert "ran out of steps" in run("read it", tab)["asked"]


def test_resuming_reports_what_he_did_while_the_agent_waited(tab, model):
    m = model(done)
    tab.url = FORM
    before = {"url": FORM, "title": "", "fields": {}, "human_last": None}
    tab.human(Email="matt@example.com")
    run("fill the form\n\nHis answer: go ahead", tab, baseline=before)
    assert '"Email" now holds "matt@example.com"' in m.prompts[0][-1]["content"]


# ===============================================================================
# The pieces underneath
# ===============================================================================
def test_human_changes_needs_evidence_and_describes_every_difference():
    now = time.time()
    b = {"url": FORM, "fields": {"Email": "a", "Name": "M"}, "human_last": None}
    a = {"url": FORM + "/2", "fields": {"Email": "b", "Zip": "95570"}, "human_last": now}
    out = agent.human_changes(b, a)
    assert any("page moved" in x for x in out)
    assert '"Email" now holds "b" (was "a")' in out and '"Zip" now holds "95570"' in out
    assert '"Name" was cleared' in out and any("typed or clicked" in x for x in out)
    assert agent.human_changes(b, dict(a, human_last=None)) == []
    assert agent.human_changes(None, a) == []


def test_human_active_window(monkeypatch):
    monkeypatch.setattr(agent, "HUMAN_QUIET", 20)
    now = time.time()
    assert agent.human_active({"human_last": now - 5}, now)
    assert not agent.human_active({"human_last": now - 25}, now)
    assert not agent.human_active({"human_last": None}, now) and not agent.human_active(None, now)


def test_page_key_and_field_names():
    assert agent.page_key("https://a.example/x/#top") == "https://a.example/x"
    assert agent._fields_dict([["Email", "a"], ["Email", "b"]]) == {"Email": "a", "Email #2": "b"}


def test_listener_and_look_scripts():
    assert "isTrusted" in agent.HUMAN_JS and "if (window.__bbHuman) return" in agent.HUMAN_JS
    assert "type === 'password'" in agent.LOOK_JS and "(hidden)" in agent.LOOK_JS
    assert "the human" in agent.SYSTEM and "ask(question)" in agent.SYSTEM


class _CmdTab(agent.CDP):
    """A real agent.CDP whose wire is replaced by a dict of canned answers."""

    def __init__(self, page):
        super().__init__()
        self.page, self.sent = page, []

    async def cmd(self, method, params=None):
        self.sent.append((method, params))
        if method.startswith("Input."):
            self.agent_inputs.append(time.time())
        if method == "Runtime.evaluate" and "LOOK" not in str(params) and "location.href" in str(params):
            import json
            return {"result": {"value": json.dumps(self.page)}}
        return {}


def test_cdp_look_discounts_its_own_input_and_reads_fields():
    now = time.time()
    t = _CmdTab({"url": FORM, "title": "Opt out", "fields": [["Email", "m@x.com"], ["pw", "(hidden)"]],
                 "human": [[now - 3, "keydown"], [now + 0.3, "pointerdown"]]})
    t.agent_inputs = [now]                              # we clicked at `now`: the second event is ours
    st = asyncio.run(t.look())
    assert st["url"] == FORM and st["fields"] == {"Email": "m@x.com", "pw": "(hidden)"}
    assert st["human_last"] == pytest.approx(now - 3)


def test_cdp_records_a_broker_refusal():
    t = agent.CDP()

    class WS:
        async def send(self, raw):
            self.mid = __import__("json").loads(raw)["id"]

        async def recv(self):
            return __import__("json").dumps({"id": self.mid, "error": {
                "code": -32000, "message": "browser-broker: the human is working in this tab"}})
    t.ws = WS()
    asyncio.run(t.cmd("Input.insertText", {"text": "x"}))
    assert t.refused.startswith("browser-broker: the human is working") and t.agent_inputs
