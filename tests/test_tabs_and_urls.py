"""Bug fixes 2026-10-29: bare hosts are normalized before the guard, and every tab the agent
caused to open is closed when the task ends (plain CDP browser and broker lease)."""
import asyncio
import json

import pytest

import agent


@pytest.mark.parametrize("raw,want", [
    ("example.com", "https://example.com"),
    ("en.wikipedia.org/wiki/Mac_mini", "https://en.wikipedia.org/wiki/Mac_mini"),
    ("localhost:3000/x", "https://localhost:3000/x"),
    ("//example.com/a", "https://example.com/a"),
    ("https://example.com", "https://example.com"),
    ("http://example.com/a", "http://example.com/a"),
    ("about:blank", "about:blank"),
])
def test_normalize_ok(raw, want):
    assert agent.normalize_url(raw) == (want, None)


@pytest.mark.parametrize("raw", [
    "javascript:alert(1)", "JavaScript:alert(1)", "file:///etc/passwd", "data:text/html,hi",
    "chrome://settings", "blob:https://x/y", "view-source:https://x", "ftp://x.com/a",
    "chrome-extension://abc/page.html",
])
def test_normalize_rejects_unsafe(raw):
    assert agent.normalize_url(raw)[1]


def test_guard_normalizes_bare_host():
    args = {"url": "en.wikipedia.org/wiki/Mac_mini"}
    assert agent.guard_check("navigate", args) is None
    assert args["url"] == "https://en.wikipedia.org/wiki/Mac_mini"


@pytest.mark.parametrize("u", ["javascript:alert(1)", "file:///etc/hosts", "data:text/html,x", "chrome://version"])
def test_guard_blocks_unsafe_schemes(u):
    assert "BLOCKED" in agent.guard_check("navigate", {"url": u})


def test_guard_still_blocks_bank_without_scheme():
    agent._GUARD["task"] = "look up the weather"
    assert "BLOCKED" in agent.guard_check("navigate", {"url": "chase.com/login"})


class _Resp:
    def __init__(self, data): self.data = data
    def read(self): return self.data
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _fake_cdp(monkeypatch, pages):
    closed = []
    def urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/json"):
            return _Resp(json.dumps([{"id": i, "type": "page", "url": "x"} for i in pages]).encode())
        if "/json/close/" in url:
            closed.append(url.rsplit("/", 1)[1]); pages.remove(url.rsplit("/", 1)[1])
            return _Resp(b"Target is closing")
        raise AssertionError(url)
    monkeypatch.setattr(agent.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(agent, "_install_exit_hooks", lambda: None)
    agent._PLAIN.clear()
    return closed


def test_plain_cdp_closes_only_new_tabs(monkeypatch):
    pages = ["human1", "human2"]
    closed = _fake_cdp(monkeypatch, pages)
    agent._plain_snapshot()
    pages += ["popup1", "popup2"]
    agent._release_tab()
    assert sorted(closed) == ["popup1", "popup2"]
    assert pages == ["human1", "human2"]
    agent._release_tab()           # safe twice
    assert sorted(closed) == ["popup1", "popup2"]


@pytest.mark.parametrize("how", ["ok", "error", "interrupt"])
def test_run_closes_tabs_on_success_failure_and_ctrl_c(monkeypatch, how):
    pages = ["human1"]
    closed = _fake_cdp(monkeypatch, pages)

    class Tab:
        async def connect(self):
            agent._plain_snapshot(); pages.append("opened")
        async def close(self): pass

    async def fake_run(task, cdp, baseline=None):
        if how == "error": raise RuntimeError("boom")
        if how == "interrupt": raise KeyboardInterrupt()
        return None

    monkeypatch.setattr(agent, "CDP", Tab)
    monkeypatch.setattr(agent, "_run", fake_run)
    if how == "ok":
        asyncio.run(agent.run("t"))
    else:
        with pytest.raises((RuntimeError, KeyboardInterrupt)):
            asyncio.run(agent.run("t"))
    assert closed == ["opened"] and pages == ["human1"]


def test_broker_lease_released_on_failure(monkeypatch):
    posts = []
    monkeypatch.setattr(agent, "_broker_post", lambda p, d: posts.append((p, d)) or {})
    agent._BROKER.update(lease_id="L1")

    class Tab:
        async def connect(self): pass
        async def close(self): pass
    async def boom(task, cdp, baseline=None): raise RuntimeError("x")
    monkeypatch.setattr(agent, "CDP", Tab)
    monkeypatch.setattr(agent, "_run", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(agent.run("t"))
    assert ("/release", {"lease_id": "L1"}) in posts
