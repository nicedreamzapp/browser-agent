"""Shared setup: import agent.py, and make sure no test can reach the outside world.

Nothing here talks to a model, a browser, a broker or a phone. The model is a script,
the browser is FakeTab, and the phone and broker hooks are switched off."""
import asyncio
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import agent  # noqa: E402


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    texts = []
    monkeypatch.setattr(agent, "_text_phone", texts.append)            # never text anyone
    monkeypatch.setattr(agent, "_broker_human_last", lambda tid: None)  # never call a real broker
    monkeypatch.setattr(agent, "_BROKER", {})
    agent._GUARD.update(page_text="", seen=False, task="", blocked=0, origin="", by_origin={}, names={})
    return texts


class FakeTab:
    """Stands in for agent.CDP: a page with a URL and form fields, and a human who can act.

    `script` maps the n-th call to look() (1-based) to a function that changes the page
    the way a person would, just before that look happens."""

    def __init__(self, url="about:blank"):
        self.url, self.title, self.fields = url, "", {}
        self.human_last = None
        self.log = []
        self.looks = 0
        self.script = {}
        self.refused = None
        self.agent_inputs = []
        self.fail_clicks = set()
        self.refuse_next = None

    # what a person does
    def human(self, ago=30.0, **fields):
        """He fills in `fields`; his last input was `ago` seconds back (default: he has
        already stopped, so the agent does not pause)."""
        self.fields.update(fields)
        self.human_last = time.time() - ago

    async def look(self):
        self.looks += 1
        if self.looks in self.script:
            self.script[self.looks](self)
        self.log.append("look")
        return {"url": self.url, "title": self.title, "fields": dict(self.fields),
                "human_last": self.human_last, "at": time.time()}

    async def navigate(self, url):
        self.log.append(("navigate", url))
        self.url = url
        return f"Navigated to {url}"

    async def snapshot(self):
        self.log.append("snapshot")
        return f'[1] textbox "Email" [2] button "Send" @ {self.url} {json.dumps(self.fields)}'

    async def click(self, uid):
        self.log.append(("click", uid))
        if self.refuse_next:
            self.refused, self.refuse_next = self.refuse_next, None
            return "Clicked"
        if str(uid) in self.fail_clicks:
            return "Error: can't resolve"
        return "Clicked"

    async def type_into(self, uid, text):
        self.log.append(("type", uid, text))
        self.fields["Email"] = text
        return f"Typed {len(text)} chars"

    async def scroll(self, d="down"):
        self.log.append(("scroll", d))
        return f"Scrolled {d}"

    async def js(self, code):
        self.log.append(("js", code))
        return "ok"

    async def close(self):
        pass

    def actions(self):
        return [e for e in self.log if isinstance(e, tuple)]


class Model:
    """A scripted model: hands out replies in order and keeps every prompt it was given."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, messages):
        self.prompts.append(json.loads(json.dumps(messages)))
        r = self.replies.pop(0) if self.replies else {"tool": "done", "args": {"message": "out of script"}}
        return json.dumps(r)

    def last_user(self):
        return self.prompts[-1][-1]["content"]


@pytest.fixture
def tab():
    return FakeTab()


@pytest.fixture
def model(monkeypatch):
    def install(*replies):
        m = Model(*replies)
        monkeypatch.setattr(agent, "ask_model", m)
        return m
    return install


def run(task, tab, baseline=None):
    return asyncio.run(agent._run(task, tab, baseline=baseline))
