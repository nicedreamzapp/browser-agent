"""Registry bookkeeping: reaping, the tabs.json record, and log rotation."""
import json
import os
import time

from conftest import http


def test_reap_closes_expired_rest_lease(stack):
    _, l = http("POST", stack.api_base + "/lease", {"owner": "a", "ttl": 300})
    tid = l["target_id"]
    stack.reg.tabs[tid]["expires"] = time.time() - 1
    stack.run(stack.reg.reap())
    assert tid not in stack.reg.tabs and tid not in stack.fake.targets


def test_reap_keeps_live_rest_lease(stack):
    _, l = http("POST", stack.api_base + "/lease", {"owner": "a", "ttl": 300})
    stack.run(stack.reg.reap())
    assert l["target_id"] in stack.reg.tabs


def test_reap_closes_pool_tab_nobody_attached(stack):
    _, t = http("PUT", stack.base + "/json/new")
    stack.reg.tabs[t["id"]]["expires"] = time.time() - 1
    stack.run(stack.reg.reap())
    assert t["id"] not in stack.fake.targets


def test_reap_leaves_attached_pool_tab_and_ws_tabs(stack):
    _, t = http("PUT", stack.base + "/json/new")
    stack.page_client(t["id"]).call("Page.enable")
    c = stack.browser_client()
    ws_tid = c.call("Target.createTarget", url="about:blank")["result"]["targetId"]
    for tid in (t["id"], ws_tid):
        stack.reg.tabs[tid]["expires"] = time.time() - 1
    stack.run(stack.reg.reap())
    assert t["id"] in stack.reg.tabs and ws_tid in stack.reg.tabs


def test_reap_forgets_tabs_that_vanished(stack):
    _, l = http("POST", stack.api_base + "/lease", {"owner": "a"})
    del stack.fake.targets[l["target_id"]]          # browser restarted under us
    stack.run(stack.reg.reap())
    assert l["target_id"] not in stack.reg.tabs
    assert json.load(open(stack.proxy.STATE)) == []


def test_reap_survives_a_dead_browser(stack):
    _, l = http("POST", stack.api_base + "/lease", {"owner": "a"})
    stack.fake.fail["Target.getTargets"] = "gone"
    stack.run(stack.reg.reap())
    assert l["target_id"] in stack.reg.tabs


def test_state_file_tracks_our_tabs(stack):
    _, a = http("POST", stack.api_base + "/lease", {"owner": "a"})
    _, b = http("PUT", stack.base + "/json/new")
    assert sorted(json.load(open(stack.proxy.STATE))) == sorted([a["target_id"], b["id"]])
    http("POST", stack.api_base + "/release", {"lease_id": a["lease_id"]})
    assert json.load(open(stack.proxy.STATE)) == [b["id"]]


def test_close_leftovers_only_touches_recorded_ids(stack):
    human = stack.fake.add_human_tab()
    orphan = stack.fake.add_human_tab(url="about:blank", title="")   # a tab we made last run
    with open(stack.proxy.STATE, "w") as f:
        json.dump([orphan, "ALREADY-GONE"], f)
    stack.run(stack.reg.close_leftovers())
    assert orphan not in stack.fake.targets
    assert human in stack.fake.targets
    assert stack.fake.params_for("Target.closeTarget") == [{"targetId": orphan}]
    assert json.load(open(stack.proxy.STATE)) == []


def test_close_leftovers_tolerates_missing_or_corrupt_record(stack):
    human = stack.fake.add_human_tab()
    os.remove(stack.proxy.STATE) if os.path.exists(stack.proxy.STATE) else None
    stack.run(stack.reg.close_leftovers())
    with open(stack.proxy.STATE, "w") as f:
        f.write("{not json")
    stack.run(stack.reg.close_leftovers())
    assert human in stack.fake.targets


def test_log_rolls_over(stack, monkeypatch):
    p = stack.proxy
    monkeypatch.setattr(p, "LOG_MAX", 100)
    with open(p.LOG, "w") as f:
        f.write("x" * 200)
    p.log("fresh line")
    assert os.path.getsize(p.LOG + ".1") == 200
    assert open(p.LOG).read().endswith("fresh line\n")


def test_log_never_raises(stack, monkeypatch, tmp_path):
    monkeypatch.setattr(stack.proxy, "LOG", str(tmp_path / "missing-dir" / "broker.log"))
    stack.proxy.log("still fine")


def test_trim_stdio_empties_big_regular_files(stack, monkeypatch, tmp_path):
    p = stack.proxy
    f = open(tmp_path / "launchd.err", "w+")
    f.write("x" * 50)
    f.flush()
    monkeypatch.setattr(p, "STDIO_MAX", 10)
    monkeypatch.setattr(p.os, "fstat", lambda fd: os.stat(f.fileno()))
    truncated = []
    monkeypatch.setattr(p.os, "ftruncate", lambda fd, n: truncated.append((fd, n)))
    monkeypatch.setattr(p.os, "lseek", lambda fd, pos, how: None)
    p._trim_stdio()
    assert truncated == [(1, 0), (2, 0)]
    f.close()


def test_upstream_reports_dropped_control_socket(stack):
    async def drop_and_send():
        await stack.proxy.UP.ws.close()
        import asyncio
        await asyncio.sleep(0.05)
        # the next call reconnects instead of failing
        return await stack.proxy.UP.send("Browser.getVersion")
    assert stack.run(drop_and_send())["product"] == "FakeChrome/1.0"


def test_upstream_error_is_raised(stack):
    stack.fake.fail["Target.getTargets"] = "nope"
    import pytest
    with pytest.raises(RuntimeError, match="nope"):
        stack.run(stack.proxy.UP.send("Target.getTargets"))
