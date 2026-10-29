"""Child tabs (2026-10-29): a tab the SITE opens from one of the broker's tabs.

That night a DevHunt listing tab stayed open in Matt's browser after an agent
reported "tabs closed". Submitting the form in its leased tab made the site open
a new tab by itself (target=_blank), which the broker had never opened and so
never closed. Now a page whose opener is a broker tab belongs to the same owner
and closes with it, under the same rules: one the human is working in stays.
"""
import time
import urllib.parse

from conftest import http


def show(stack, **body):
    body.setdefault("dwell", 0)
    return http("POST", stack.api_base + "/show", body, timeout=30)


def lease(stack, **body):
    return http("POST", stack.api_base + "/lease", body)


def release(stack, lease_id):
    return http("POST", stack.api_base + "/release", {"lease_id": lease_id})


def unshow(stack, match):
    return http("POST", stack.api_base + "/unshow", {"match": match})


def child(stack, opener, url="https://devhunt.example/listing/42", announce=True):
    tid = stack.run(stack.fake.open_child(opener, url, announce))
    if announce:
        assert stack.wait_for(lambda: tid in stack.reg.children), "child tab never recorded"
    return tid


def human_types(stack, tid, ago=0.0):
    assert stack.wait_for(lambda: stack.fake.watchers.get(tid)), "the listener was never installed"
    t = stack.run(stack.fake.human(tid, "keydown", time.time() - ago))
    assert stack.wait_for(lambda: stack.proxy.HUMANS.last(tid) == t)
    return t


def test_child_of_a_leased_tab_is_closed_on_release(stack):
    _, l = lease(stack, owner="devhunt", url="https://devhunt.example/submit")
    kid = child(stack, l["target_id"])
    assert stack.reg.children[kid]["owner"] == "devhunt"
    code, r = release(stack, l["lease_id"])
    assert code == 200 and r["tab_closed"] is True
    assert [c["target"] for c in r["children_closed"]] == [kid[:12]]
    assert kid not in stack.fake.targets and l["target_id"] not in stack.fake.targets
    assert kid not in stack.reg.children


def test_grandchild_and_a_missed_event_are_still_closed_on_release(stack):
    _, l = lease(stack, owner="oauth", url="https://app.example/login")
    kid = child(stack, l["target_id"], "https://accounts.example/popup")
    grand = child(stack, kid, "https://accounts.example/consent", announce=False)
    _, r = release(stack, l["lease_id"])
    assert {c["target"] for c in r["children_closed"]} == {kid[:12], grand[:12]}
    assert kid not in stack.fake.targets and grand not in stack.fake.targets


def test_child_of_a_lease_expiring_is_closed_by_the_reaper(stack):
    _, l = lease(stack, owner="exp", url="https://site.example/a", ttl=1)
    kid = child(stack, l["target_id"])
    stack.reg.tabs[l["target_id"]]["expires"] = time.time() - 1
    stack.run(stack.reg.reap())
    assert kid not in stack.fake.targets and l["target_id"] not in stack.fake.targets


def test_child_of_a_bb_show_owner_is_closed_on_bb_close(stack):
    _, r = show(stack, owner="lister", url="https://devhunt.example/submit")
    kid = child(stack, r["target_id"])
    assert stack.reg.children[kid]["owner"] == "lister"
    code, u = unshow(stack, "lister")
    assert code == 200
    closed = {c["target"]: c for c in u["closed"]}
    assert set(closed) == {r["target_id"][:12], kid[:12]}
    assert closed[kid[:12]]["child_of"] == r["target_id"][:12]
    assert kid not in stack.fake.targets and r["target_id"] not in stack.fake.targets


def test_child_whose_parent_is_gone_is_still_closed_by_bb_close_owner(stack):
    _, l = lease(stack, owner="lister", url="https://devhunt.example/submit")
    kid = child(stack, l["target_id"])
    stack.run(stack.fake._destroy(l["target_id"]))           # he closed the parent himself
    stack.run(stack.reg.reap())                              # the broker notices it is gone
    assert l["target_id"] not in stack.reg.tabs and kid in stack.reg.children
    _, u = unshow(stack, "lister")
    assert [c["target"] for c in u["closed"]] == [kid[:12]]
    assert kid not in stack.fake.targets


def test_child_the_human_is_working_in_is_left_open(stack):
    _, l = lease(stack, owner="devhunt", url="https://devhunt.example/submit")
    kid = child(stack, l["target_id"])
    human_types(stack, kid)
    _, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is True                           # the parent he never touched still goes
    assert [c["target"] for c in r["children_left_open"]] == [kid[:12]]
    assert "working" in r["children_left_open"][0]["why"]
    assert kid in stack.fake.targets and kid not in stack.reg.children


def test_child_of_a_shown_tab_the_human_types_in_is_left_open_on_bb_close(stack):
    _, s = show(stack, owner="lister", url="https://devhunt.example/submit")
    kid = child(stack, s["target_id"])
    human_types(stack, kid)
    _, u = unshow(stack, "lister")
    assert [c["target"] for c in u["closed"]] == [s["target_id"][:12]]
    assert [c["target"] for c in u["left_open"]] == [kid[:12]]
    assert kid in stack.fake.targets


def test_children_of_a_tab_left_for_the_human_stay_with_it(stack):
    _, l = lease(stack, owner="devhunt", url="https://devhunt.example/submit")
    kid = child(stack, l["target_id"])
    human_types(stack, l["target_id"])
    _, r = release(stack, l["lease_id"])
    assert r["tab_closed"] is False
    assert [c["target"] for c in r["children_left_open"]] == [kid[:12]]
    assert kid in stack.fake.targets and l["target_id"] in stack.fake.targets


def test_tab_with_no_tracked_opener_is_untouched(stack):
    his = stack.fake.add_human_tab("https://news.example/")
    from_his = stack.run(stack.fake.open_child(his, "https://news.example/story"))
    _, l = lease(stack, owner="devhunt", url="https://devhunt.example/submit")
    _, s = show(stack, owner="lister", url="https://other.example/")
    time.sleep(0.2)
    assert from_his not in stack.reg.children
    release(stack, l["lease_id"])
    _, u = unshow(stack, "lister")
    assert his in stack.fake.targets and from_his in stack.fake.targets
    assert all(c["target"] != from_his[:12] for c in u["closed"] + u["left_open"])
    _, st = http("GET", stack.api_base + "/status")
    assert st["children"] == []


def test_status_and_peek_list_children_under_their_owner(stack):
    _, l = lease(stack, owner="devhunt", url="https://devhunt.example/submit")
    kid = child(stack, l["target_id"])
    _, st = http("GET", stack.api_base + "/status")
    lease_row = [x for x in st["leases"] if x["lease_id"] == l["lease_id"]][0]
    assert lease_row["children"] == [{"target": kid[:12], "url": "https://devhunt.example/listing/42"}]
    assert st["children"][0]["owner"] == "devhunt" and st["children"][0]["child_of"] == l["target_id"][:12]
    _, p = http("GET", stack.api_base + "/peek?" + urllib.parse.urlencode({"match": "devhunt"}))
    by = {t["target"]: t for t in p["tabs"]}
    assert set(by) == {l["target_id"][:12], kid[:12]}
    assert by[kid[:12]]["child_of"] == l["target_id"][:12] and by[kid[:12]]["opened_by_broker"] is True
    assert by[l["target_id"][:12]]["children"][0]["target"] == kid[:12]


def test_child_the_human_opened_with_his_own_click_is_left_open(stack):
    _, s = show(stack, owner="lister", url="https://devhunt.example/submit")
    t = human_types(stack, s["target_id"], ago=30)           # his click, long enough ago to be quiet
    kid = child(stack, s["target_id"])
    stack.reg.children[kid]["created"] = t + 0.5             # the tab appeared right after it
    _, u = unshow(stack, "lister")
    assert [c["target"] for c in u["closed"]] == [s["target_id"][:12]]
    assert [(c["target"], c["why"]) for c in u["left_open"]] == [(kid[:12], "he opened it himself")]
    assert kid in stack.fake.targets


def test_tabs_opened_from_the_humans_own_tab_shown_for_a_job_are_not_recorded(stack):
    his = stack.fake.add_human_tab("https://shop.example/orders")
    _, s = show(stack, owner="orders", url="https://shop.example/orders")
    assert s["target_id"] == his                             # his own tab, brought forward
    from_his = stack.run(stack.fake.open_child(his, "https://shop.example/orders/7"))
    time.sleep(0.2)
    assert from_his not in stack.reg.children
    unshow(stack, "orders")
    assert his in stack.fake.targets and from_his in stack.fake.targets
