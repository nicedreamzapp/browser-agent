"""The Broker Recorder, end to end through the real proxy and the fake browser."""
import glob
import json
import os
import stat
import struct
import time
import zlib

import pytest
import websocket

import recorder
import scrub
from conftest import CDPClient, http

EMAIL_BOX = [100, 100, 300, 40]
PW_BOX = [100, 160, 300, 40]
BTN_BOX = [100, 220, 120, 40]


def login_page(focused=None):
    return {"url": "https://shop.example.com/login?session=Zx81Qp0LmNa7", "title": "Sign in",
            "focused": focused, "masks": [],
            "els": [
                {"id": "e0", "tag": "h1", "role": "heading", "name": "Sign in to your account", "box": [100, 40, 400, 40]},
                {"id": "e1", "tag": "input", "role": "textbox", "name": "Email", "box": EMAIL_BOX,
                 "field": True, "type": "email", "fname": "email", "fid": "email", "ac": "username", "filled": False},
                {"id": "e2", "tag": "input", "role": "textbox", "name": "Password", "box": PW_BOX,
                 "field": True, "type": "password", "fname": "pw", "fid": "pw", "ac": "current-password",
                 "filled": False},
                {"id": "e3", "tag": "button", "role": "button", "name": "Sign in", "box": BTN_BOX},
            ]}


def start_recorder(stack, tmp_path, **kw):
    kw.setdefault("all_jobs", True)
    rec = recorder.Recorder(lambda: stack.proxy.UPSTREAM, out_dir=str(tmp_path / "rec"), **kw)
    stack.proxy.REC = rec
    return rec


@pytest.fixture
def rec_stack(stack, tmp_path, monkeypatch):
    # a recorder made on demand (a lease or header asking) must never write to the real ~/.local
    monkeypatch.setenv("BROKER_RECORD_DIR", str(tmp_path / "rec"))
    made = []

    def make(**kw):
        r = start_recorder(stack, tmp_path, **kw)
        made.append(r)
        return r
    yield stack, make
    for r in made:
        r.stop()
    if stack.proxy.REC is not None and stack.proxy.REC not in made:
        stack.proxy.REC.stop()


def read_jobs(rec):
    out = {}
    for f in glob.glob(os.path.join(rec.dir, "*", "*.jsonl")):
        with open(f) as fh:
            lines = [json.loads(line) for line in fh]
        out[lines[0]["job"]] = (f, lines)
    return out


def all_text(rec):
    s = ""
    for f in glob.glob(os.path.join(rec.dir, "**", "*"), recursive=True):
        if os.path.isfile(f) and f.endswith(".jsonl"):
            s += open(f).read()
    return s


def key(c, text):
    for ch in text:
        c.call("Input.dispatchKeyEvent", type="keyDown", key=ch, text=ch)
        c.call("Input.dispatchKeyEvent", type="keyUp", key=ch)


def click(c, box):
    x, y = box[0] + box[2] / 2, box[1] + box[3] / 2
    c.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
    c.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
    c.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)


def wait_obs(stack, rec, tid):
    assert stack.wait_for(lambda: tid in rec.worker.obs, 5)


# ---- off --------------------------------------------------------------------------
def scenario(stack):
    """A fixed little job. Returns (frames the client got, what the browser got) with ids normalised."""
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "same", "url": "about:blank"})
    tid = lease["target_id"]
    c = stack.page_client(tid)
    got = []
    cmds = [("Page.navigate", {"url": "https://example.com/?token=abc"}),
            ("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 5, "y": 5}),
            ("Input.insertText", {"text": "hello"}),
            ("Network.setExtraHTTPHeaders", {"headers": {"Authorization": "Bearer x"}}),
            ("Runtime.evaluate", {"expression": "1+1"}),
            ("Page.reload", {})]
    for i, (m, p) in enumerate(cmds, 1):
        c.ws.send(json.dumps({"id": i, "method": m, "params": p}))
        got.append(c.ws.recv())
    http("POST", stack.api_base + "/release", {"lease_id": lease["lease_id"]})
    norm = lambda s: s.replace(tid, "TID")
    sent = sorted((ch.split(":")[0], m, json.dumps(p, sort_keys=True))
                  for ch, m, p in stack.fake.calls
                  if not ch.startswith("rec:") and ch != "browser")
    return [norm(g) for g in got], [tuple(norm(x) for x in s) for s in sent]


def test_off_by_default_and_traffic_identical_when_on(rec_stack, tmp_path):
    stack, make = rec_stack
    assert stack.proxy.REC is None
    off = scenario(stack)
    assert not os.path.exists(os.path.expanduser(str(tmp_path / "rec")))
    stack.fake.calls.clear()
    rec = make()
    on = scenario(stack)
    assert off == on                                    # byte for byte, both directions
    assert rec.flush()
    assert len(read_jobs(rec)) == 1


def test_off_means_no_recorder_thread(stack):
    import threading
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "a"})
    c = stack.page_client(lease["target_id"])
    c.call("Input.insertText", text="x")
    assert stack.proxy.REC is None
    assert not [t for t in threading.enumerate() if t.name == "broker-recorder"]


def test_lease_without_record_flag_is_not_recorded_when_env_off(rec_stack, tmp_path):
    stack, make = rec_stack
    rec = make(all_jobs=False)
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "a"})
    c = stack.page_client(lease["target_id"])
    c.call("Input.insertText", text="x")
    http("POST", stack.api_base + "/release", {"lease_id": lease["lease_id"]})
    assert rec.flush()
    assert read_jobs(rec) == {}


# ---- on ---------------------------------------------------------------------------
def test_a_login_job_becomes_a_clean_trajectory(rec_stack, tmp_path):
    stack, make = rec_stack
    stack.proxy.REC = None
    _, lease = http("POST", stack.api_base + "/lease",
                    {"owner": "agent1", "record": True, "task": "Sign in to the shop as matt@example.com"})
    rec = stack.proxy.REC
    assert rec is not None and not rec.all_jobs
    tid = lease["target_id"]
    stack.fake.snapshots[tid] = login_page()
    wait_obs(stack, rec, tid)
    c = stack.page_client(tid)
    c.call("Page.navigate", url="https://shop.example.com/login?session=Zx81Qp0LmNa7&next=/account")
    click(c, EMAIL_BOX)
    key(c, "matt@example.com")
    click(c, PW_BOX)
    c.call("Input.insertText", text="Hunter2!xyz")
    c.call("Input.dispatchKeyEvent", type="keyDown", key="Enter", text="\r")
    c.call("Runtime.evaluate", expression="document.querySelector('#pw').value = 'An0ther$ecret'")
    c.call("Network.setExtraHTTPHeaders", headers={"Authorization": "Bearer SUPERSECRETTOKEN1"})
    c.call("Network.getCookies")
    c.call("DOM.setFileInputFiles", files=["/Users/someone/Private/id-scan.png"], nodeId=4)
    http("POST", stack.api_base + "/release", {"lease_id": lease["lease_id"], "outcome": "success"})
    assert stack.wait_for(lambda: not rec.worker.jobs, 5)
    assert rec.flush()

    jobs = read_jobs(rec)
    path, lines = jobs[lease["lease_id"]]
    head, steps, end = lines[0], [x for x in lines if x["type"] == "step"], lines[-1]
    assert head["type"] == "job" and head["task"] == "Sign in to the shop as [EMAIL]"
    assert end["type"] == "end" and end["outcome"] == "success" and end["outcome_source"] == "client"
    acts = [(s["action"]["type"], (s["target"] or {}).get("name")) for s in steps]
    assert acts == [("navigate", None), ("click", "Email"), ("type", "Email"), ("click", "Password"),
                    ("type", "Password"), ("key", "Password"), ("js", "Password"), ("upload", "Password")]
    by = {s["action"]["type"] + str(i): s for i, s in enumerate(steps)}
    typed = [s for s in steps if s["action"]["type"] == "type"]
    assert typed[0]["action"]["text"] == "[EMAIL]"          # coalesced keystrokes, then masked
    assert typed[1]["action"]["text"] == scrub.REDACTED and typed[1]["action"]["redacted"]
    assert typed[1]["target"]["sensitive"] is True
    assert all(s["actor"] == "agent" for s in steps)
    assert steps[0]["observation"]["elements"][2]["input_type"] == "password"
    assert [s["seq"] for s in steps] == sorted(s["seq"] for s in steps)
    assert by["upload7"]["action"]["files"] == ["id-scan.png"]
    text = open(path).read()
    for secret in ("matt@example.com", "Hunter2", "An0ther$ecret", "SUPERSECRETTOKEN1", "Zx81Qp0LmNa7",
                   "/Users/someone", "Network.", "current-password", '"pw"'):
        assert secret not in text, secret
    # private files
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(rec.dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700


def test_unknown_target_password_like_typing_is_redacted(rec_stack):
    stack, make = rec_stack
    rec = make()
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "a"})
    tid = lease["target_id"]
    stack.fake.snapshots[tid] = {"els": [], "masks": []}
    wait_obs(stack, rec, tid)
    c = stack.page_client(tid)
    key(c, "Tr0ub4dor&3")
    http("POST", stack.api_base + "/release", {"lease_id": lease["lease_id"]})
    assert stack.wait_for(lambda: not rec.worker.jobs, 5) and rec.flush()
    t = all_text(rec)
    assert "Tr0ub4dor" not in t and "Tr0" not in t and scrub.REDACTED in t


def test_refusals_and_the_humans_help_are_recorded(rec_stack):
    stack, make = rec_stack
    rec = make()
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "a", "task": "fill the form"})
    tid = lease["target_id"]
    stack.fake.snapshots[tid] = login_page(focused="e1")
    assert stack.wait_for(lambda: stack.fake.watchers.get(tid), 3)
    wait_obs(stack, rec, tid)
    stack.run(stack.fake.human(tid, "keydown"))
    stack.run(stack.fake.human(tid, "keydown", time.time() + 0.3))
    assert stack.wait_for(lambda: stack.proxy.HUMANS.active(tid))
    c = stack.page_client(tid)
    r = c.call("Input.insertText", text="hello")
    assert "error" in r
    http("POST", stack.api_base + "/record", {"lease_id": lease["lease_id"], "outcome": "partial",
                                               "note": "the human finished it"})
    stack.proxy.HUMANS.events.clear()
    http("POST", stack.api_base + "/release", {"lease_id": lease["lease_id"]})
    assert stack.wait_for(lambda: not rec.worker.jobs, 5) and rec.flush()
    _, lines = read_jobs(rec)[lease["lease_id"]]
    steps = [x for x in lines if x["type"] == "step"]
    human = [s for s in steps if s["actor"] == "human"]
    assert human and human[0]["action"]["type"] == "key" and human[0]["action"]["count"] == 2
    assert human[0]["target"]["name"] == "Email" and "text" not in human[0]["action"]
    agent = [s for s in steps if s["actor"] == "agent"]
    assert agent[0]["result"]["ok"] is False and agent[0]["result"]["refused_by_broker"]
    assert lines[-1]["outcome"] == "partial" and lines[-1]["note"] == "the human finished it"


def test_browser_client_job_with_task_header(rec_stack):
    stack, make = rec_stack
    stack.proxy.REC = None
    c = CDPClient.__new__(CDPClient)
    c.ws = websocket.create_connection(f"ws://127.0.0.1:{stack.port}/devtools/browser/x", suppress_origin=True,
                                       timeout=5, header=["X-Broker-Record: 1",
                                                          "X-Broker-Task: Find%20the%20Ruby%20Twist%20price"])
    c._id, c.events = 0, []
    stack.clients.append(c)
    r = c.call("Target.createTarget", url="https://ineedhemp.com/")
    tid = r["result"]["targetId"]
    rec = stack.proxy.REC
    assert rec is not None
    sid = c.call("Target.attachToTarget", targetId=tid, flatten=True)["result"]["sessionId"]
    c.call("Page.navigate", session_id=sid, url="https://ineedhemp.com/product/ruby-twist/")
    c.call("Runtime.evaluate", session_id=sid, expression="document.title")
    c.call("Network.enable", session_id=sid)
    c.close()
    assert stack.wait_for(lambda: not rec.worker.jobs, 5) and rec.flush()
    (_, lines), = read_jobs(rec).values()
    assert lines[0]["task"] == "Find the Ruby Twist price" and lines[0]["owner_kind"] == "ws"
    steps = [x for x in lines if x["type"] == "step"]
    assert [s["action"]["type"] for s in steps] == ["open_tab", "navigate", "js"]
    assert steps[1]["action"]["url"] == "https://ineedhemp.com/product/ruby-twist/"
    assert all(s["result"]["ok"] for s in steps)
    assert lines[-1]["end_reason"] == "client disconnected"


def test_a_full_queue_drops_and_never_stalls_traffic(rec_stack):
    stack, make = rec_stack
    rec = make(queue_max=3, start=False)                 # nobody drains it
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "a"})
    c = stack.page_client(lease["target_id"])
    t0 = time.time()
    for i in range(50):
        assert "result" in c.call("Input.insertText", text=str(i))
    assert time.time() - t0 < 5
    assert rec.dropped > 0


# ---- screenshots ----------------------------------------------------------------
def make_png(w, h, rgba=(255, 255, 255, 255), filt=0):
    px = bytes(rgba) * w
    rows = []
    prev = bytes(len(px))
    for _ in range(h):
        line = bytearray(px)
        if filt == 1:
            enc = bytearray(line[i] - (line[i - 4] if i >= 4 else 0) & 0xff for i in range(len(line)))
        elif filt == 2:
            enc = bytearray((a - b) & 0xff for a, b in zip(line, prev))
        elif filt == 3:
            enc = bytearray((line[i] - (((line[i - 4] if i >= 4 else 0) + prev[i]) >> 1)) & 0xff
                            for i in range(len(line)))
        elif filt == 4:
            enc = bytearray()
            for i in range(len(line)):
                a = line[i - 4] if i >= 4 else 0
                b = prev[i]
                c = prev[i - 4] if i >= 4 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                enc.append((line[i] - pr) & 0xff)
        else:
            enc = line
        rows.append(bytes([filt]) + bytes(enc))
        prev = bytes(line)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + recorder._chunk(b"IHDR", ihdr)
            + recorder._chunk(b"IDAT", zlib.compress(b"".join(rows))) + recorder._chunk(b"IEND", b""))


def read_png(data):
    pos, idat, ihdr = 8, [], None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            ihdr = body
        elif typ == b"IDAT":
            idat.append(body)
    w, h = struct.unpack(">II", ihdr[:8])
    raw = zlib.decompress(b"".join(idat))
    stride = w * 4
    rows = []
    for y in range(h):
        assert raw[y * (stride + 1)] == 0
        rows.append(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
    return lambda x, y: tuple(rows[y][x * 4:x * 4 + 4])


@pytest.mark.parametrize("filt", [0, 1, 2, 3, 4])
def test_png_blackout_every_filter(filt):
    png = make_png(20, 12, (200, 100, 50, 255), filt)
    out = recorder.png_blackout(png, [[8, 6, 4, 2]], scale=1.0)
    px = read_png(out)
    assert px(10, 7) == (0, 0, 0, 255)
    assert px(0, 0) == (200, 100, 50, 255) and px(19, 11) == (200, 100, 50, 255)


def test_png_blackout_refuses_what_it_cannot_rewrite():
    assert recorder.png_blackout(b"not a png", [[0, 0, 1, 1]]) is None
    png = make_png(4, 4)
    bad = png.replace(struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0), struct.pack(">IIBBBBB", 4, 4, 16, 6, 0, 0, 0))
    assert recorder.png_blackout(bad, [[0, 0, 1, 1]]) is None


def test_screenshot_is_saved_with_fields_blacked_out(rec_stack):
    stack, make = rec_stack
    rec = make(screenshots=True)
    stack.fake.screenshot = make_png(40, 30, (255, 255, 255, 255), 1)
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "a"})
    tid = lease["target_id"]
    page = login_page()
    page["masks"] = [[20, 20, 20, 10]]          # css px; the shot is taken at scale 0.5
    stack.fake.snapshots[tid] = page
    assert stack.wait_for(lambda: (rec.worker.obs.get(tid) or {}).get("shot"), 5)
    c = stack.page_client(tid)
    c.call("Page.reload")
    http("POST", stack.api_base + "/release", {"lease_id": lease["lease_id"]})
    assert stack.wait_for(lambda: not rec.worker.jobs, 5) and rec.flush()
    _, lines = read_jobs(rec)[lease["lease_id"]]
    step = [x for x in lines if x["type"] == "step"][0]
    full = os.path.join(rec.dir, step["screenshot"])
    assert stat.S_IMODE(os.stat(full).st_mode) == 0o600
    px = read_png(open(full, "rb").read())
    assert px(12, 12) == (0, 0, 0, 255)
    assert px(2, 2) == (255, 255, 255, 255)


# ---- export --------------------------------------------------------------------
def test_export_to_sft_and_stats(rec_stack, tmp_path, capsys):
    import record_export
    stack, make = rec_stack
    rec = make()
    _, lease = http("POST", stack.api_base + "/lease", {"owner": "a", "task": "Log in and open the account page"})
    tid = lease["target_id"]
    stack.fake.snapshots[tid] = login_page()
    wait_obs(stack, rec, tid)
    c = stack.page_client(tid)
    click(c, EMAIL_BOX)
    key(c, "matt@example.com")
    click(c, PW_BOX)
    c.call("Input.insertText", text="Hunter2!xyz")
    click(c, BTN_BOX)
    http("POST", stack.api_base + "/release", {"lease_id": lease["lease_id"], "outcome": "success"})
    assert stack.wait_for(lambda: not rec.worker.jobs, 5) and rec.flush()

    out = tmp_path / "train.jsonl"
    assert record_export.main([rec.dir, "-o", str(out), "--allow-stale"]) == 0
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    calls = [(r["messages"][2]["tool_calls"][0]["function"]["name"],
              json.loads(r["messages"][2]["tool_calls"][0]["function"]["arguments"])) for r in rows]
    assert calls == [("click", {"element": "e1"}), ("type", {"element": "e1", "text": "[EMAIL]"}),
                     ("click", {"element": "e2"}), ("click", {"element": "e3"})]   # the password step is left out
    user = rows[0]["messages"][1]["content"]
    assert user.startswith("Task: Log in and open the account page")
    assert '[e2] textbox "Password" (password, empty, sensitive)' in user
    assert "Previous actions:\n1. click(element=\"e1\")" in rows[1]["messages"][1]["content"]
    assert rows[0]["tools"] and stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert "Hunter2" not in out.read_text() and "matt@example.com" not in out.read_text()

    # stale snapshots are left out unless asked for
    out2 = tmp_path / "strict.jsonl"
    record_export.main([rec.dir, "-o", str(out2)])
    assert len(out2.read_text().splitlines()) < len(rows)

    out3 = tmp_path / "lfm.jsonl"
    record_export.main([rec.dir, "-o", str(out3), "--allow-stale", "--format", "lfm"])
    first = json.loads(out3.read_text().splitlines()[0])
    assert first["messages"][2]["content"] == '<|tool_call_start|>[click(element="e1")]<|tool_call_end|>'
    assert "<|tool_list_start|>" in first["messages"][0]["content"]

    capsys.readouterr()
    record_export.main([rec.dir, "--stats", "--allow-stale"])
    s = json.loads(capsys.readouterr().out)
    assert s["jobs"] == 1 and s["outcomes"] == {"success": 1} and s["redacted_steps"] == 1
    assert s["by_action"] == {"click": 3, "type": 2} and s["samples_exportable"] == 4
