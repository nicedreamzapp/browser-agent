"""bb-desktop: the desktop lease on the virtual Mac, against a fake terminal-mac on PATH."""
import importlib.machinery, importlib.util, json, os, stat, subprocess, sys, time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bb-desktop")

FAKE_TMAC = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_TMAC_LOG"], "a") as f:
    f.write(json.dumps(a) + "\n")
vm_state = open(os.environ["FAKE_TMAC_STATE"]).read().strip()
if a[0] == "status":
    print(json.dumps({"name": a[1], "state": vm_state})); sys.exit(0)
if a[0] == "resume":
    open(os.environ["FAKE_TMAC_STATE"], "w").write("running"); print("{}"); sys.exit(0)
if a[0] == "pause":
    open(os.environ["FAKE_TMAC_STATE"], "w").write("paused"); print("{}"); sys.exit(0)
if a[0] == "ls":
    print(json.dumps([{"name": "default", "installed": True}])); sys.exit(0)
if a[0] == "action":
    verb = a[2]
    if verb == "status":
        perm = os.environ.get("FAKE_TMAC_PERMS", "true")
        print(json.dumps({"accessibility": perm, "screenRecording": "true", "width": "1280", "height": "800"}))
    elif verb == "screenshot":
        open(a[a.index("--output") + 1], "wb").write(b"\x89PNG fake")
        print(json.dumps({"path": a[a.index("--output") + 1]}))
    elif verb == "key" and a[3] == "fail":
        print("Unknown key", file=sys.stderr); sys.exit(1)
    else:
        print("{}")
    sys.exit(0)
sys.exit(2)
'''


@pytest.fixture
def env(tmp_path):
    fake = tmp_path / "bin" / "terminal-mac"
    fake.parent.mkdir()
    fake.write_text(FAKE_TMAC)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "vmstate").write_text("running")
    e = dict(os.environ)
    e.pop("BB_DESKTOP_TMAC", None)
    e.pop("BB_DESKTOP_NATIVE_TYPE", None)
    e.update(PATH=str(fake.parent) + os.pathsep + e["PATH"],
             FAKE_TMAC_LOG=str(tmp_path / "calls.log"), FAKE_TMAC_STATE=str(tmp_path / "vmstate"),
             BB_DESKTOP_STATE=str(tmp_path / "lease.json"), BB_DESKTOP_LOG=str(tmp_path / "desktop.log"),
             BB_DESKTOP_SHOTS=str(tmp_path / "shots"), BB_DESKTOP_TMAC=str(fake),
             BB_DESKTOP_PS_FILE=str(tmp_path / "ps.txt"))
    (tmp_path / "ps.txt").write_text("/sbin/launchd\n/Users/x/.local/lib/terminal-mac/TerminalMac.app/Contents/MacOS/"
                                     "terminal-mac __run default\n/Users/x/.local/bin/terminal-mac mcp\n")
    e["tmp"] = str(tmp_path)
    return e


def run(env, *args):
    p = subprocess.run([sys.executable, BIN, *args], capture_output=True, text=True, env=env, timeout=30)
    reply = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    return p.returncode, reply, p.stderr


def calls(env):
    try:
        return [json.loads(l) for l in open(env["FAKE_TMAC_LOG"])]
    except FileNotFoundError:
        return []


def clear_calls(env):
    open(env["FAKE_TMAC_LOG"], "w").close()


def load_module():
    loader = importlib.machinery.SourceFileLoader("bb_desktop", BIN)
    spec = importlib.util.spec_from_loader("bb_desktop", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def test_lease_is_exclusive(env):
    rc, r, _ = run(env, "lease", "agent-a", "--purpose", "app test")
    assert rc == 0 and r["leased"] and r["owner"] == "agent-a"
    rc, r, err = run(env, "lease", "agent-b", "--purpose", "other")
    assert rc == 4
    assert r["held_by"] == "agent-a" and r["purpose"] == "app test"
    assert "agent-a" in err and "app test" in err
    # the holder leasing again is a renew, not a refusal
    rc, r, _ = run(env, "lease", "agent-a")
    assert rc == 0 and r.get("already_yours")


def test_expired_lease_is_free_and_logged(env):
    assert run(env, "lease", "agent-a", "--ttl", "1")[0] == 0
    time.sleep(1.2)
    rc, r, _ = run(env, "lease", "agent-b")
    assert rc == 0 and r["owner"] == "agent-b"
    log = open(env["BB_DESKTOP_LOG"]).read()
    assert "lease of agent-a" in log and "expired" in log


def test_stuck_owner_is_refused_with_exit_5(env):
    for _ in range(2):
        assert run(env, "lease", "looper")[0] == 0
        assert run(env, "release", "looper")[0] == 0
    rc, r, err = run(env, "lease", "looper")
    assert rc == 5 and "ask" in err.lower()
    # someone else is not affected
    assert run(env, "lease", "fresh")[0] == 0


def test_paused_vm_is_resumed_and_missing_permissions_refused(env, tmp_path):
    open(env["FAKE_TMAC_STATE"], "w").write("paused")
    assert run(env, "lease", "a")[0] == 0
    assert ["resume", "default"] in calls(env)
    run(env, "release", "a")
    e = dict(env, FAKE_TMAC_PERMS="false")
    rc, r, err = run(e, "lease", "b")
    assert rc == 2 and "permission" in err
    open(env["FAKE_TMAC_STATE"], "w").write("stopped")
    rc, r, err = run(env, "lease", "c")
    assert rc == 2 and r["vm_state"] == "stopped"


def test_type_mapping():
    m = load_module()
    assert m.keys_for("Hi there.") == ["shift+h", "i", "space", "t", "h", "e", "r", "e", "."]
    assert m.keys_for("A1!?") == ["shift+a", "1", "shift+1", "shift+/"]
    assert m.keys_for('a,b;c:"d"\n') == ["a", ",", "b", ";", "c", "shift+;", "shift+'", "d", "shift+'", "return"]
    assert m.keys_for("(x)+{y}_|<>@#$%^&*") == [
        "shift+9", "x", "shift+0", "shift+=", "shift+[", "y", "shift+]", "shift+-", "shift+\\",
        "shift+,", "shift+.", "shift+2", "shift+3", "shift+4", "shift+5", "shift+6", "shift+7", "shift+8"]
    with pytest.raises(m.Exit) as e:
        m.keys_for("café `x`")
    assert "é" in e.value.msg and "`" in e.value.msg


def test_type_goes_key_by_key_and_log_hides_the_text(env):
    run(env, "lease", "typist")
    clear_calls(env)
    rc, r, _ = run(env, "act", "typist", "type", "Ok go")
    assert rc == 0 and r["typed_chars"] == 5
    assert calls(env) == [["action", "default", "key", k] for k in ("shift+o", "k", "space", "g", "o")]
    log = open(env["BB_DESKTOP_LOG"]).read()
    assert "len=5" in log and "Ok go" not in log
    # unmappable text sends nothing at all
    clear_calls(env)
    rc, _, err = run(env, "act", "typist", "type", "naïve")
    assert rc == 1 and calls(env) == [] and "nothing was typed" in err
    # the switch back to terminal-mac's own type
    rc, _, _ = run(dict(env, BB_DESKTOP_NATIVE_TYPE="1"), "act", "typist", "type", "Ok go")
    assert rc == 0 and calls(env)[-1] == ["action", "default", "type", "--", "Ok go"]


def test_act_passes_through_for_the_holder_only(env):
    run(env, "lease", "holder")
    clear_calls(env)
    rc, r, err = run(env, "act", "intruder", "click", "10", "20")
    assert rc == 3 and "holder" in err
    assert calls(env) == []
    assert run(env, "act", "holder", "click", "10", "20", "--double")[0] == 0
    assert run(env, "act", "holder", "scroll", "--dy", "-300", "--x", "5", "--y", "6")[0] == 0
    assert run(env, "act", "holder", "drag", "1", "2", "3", "4")[0] == 0
    assert run(env, "act", "holder", "key", "cmd+space")[0] == 0
    assert calls(env) == [["action", "default", "click", "10", "20", "--double"],
                          ["action", "default", "scroll", "--dy", "-300", "--x", "5", "--y", "6"],
                          ["action", "default", "drag", "1", "2", "3", "4"],
                          ["action", "default", "key", "cmd+space"]]
    rc, r, _ = run(env, "act", "holder", "screenshot", os.path.join(env["tmp"], "s.png"))
    assert rc == 0 and open(r["screenshot"], "rb").read().startswith(b"\x89PNG")
    # a terminal-mac failure is exit 1 and logged
    rc, _, _ = run(env, "act", "holder", "key", "fail")
    assert rc == 1 and "FAILED" in open(env["BB_DESKTOP_LOG"]).read()
    # release by a non-holder is refused; by the holder it frees the desktop
    assert run(env, "release", "intruder")[0] == 3
    assert run(env, "release", "holder")[0] == 0
    assert run(env, "act", "holder", "key", "a")[0] == 3


def test_act_renews_the_ttl(env):
    run(env, "lease", "a", "--ttl", "2")
    before = json.load(open(env["BB_DESKTOP_STATE"]))["default"]["lease"]["expires"]
    time.sleep(1.1)
    run(env, "act", "a", "key", "a")
    after = json.load(open(env["BB_DESKTOP_STATE"]))["default"]["lease"]["expires"]
    assert after > before + 1


def test_peek_is_read_only(env):
    run(env, "lease", "a", "--purpose", "look test")
    clear_calls(env)
    state_before = open(env["BB_DESKTOP_STATE"]).read()
    rc, r, _ = run(env, "peek")
    assert rc == 0 and r["held_by"] == "a" and r["purpose"] == "look test" and r["vm_state"] == "running"
    assert os.path.exists(r["screenshot"])
    verbs = [c[2] if c[0] == "action" else c[0] for c in calls(env)]
    assert set(verbs) <= {"status", "screenshot"}
    assert json.loads(open(env["BB_DESKTOP_STATE"]).read()) == json.loads(state_before)
    run(env, "release", "a")
    rc, r, _ = run(env, "peek")
    assert r["free"] and "released" in r["last"]


def test_local_when_terminal_mac_exists_else_remote(env, tmp_path, monkeypatch):
    m = load_module()
    monkeypatch.setenv("BB_DESKTOP_TMAC", env["BB_DESKTOP_TMAC"])
    assert m.local_tmac() == env["BB_DESKTOP_TMAC"]
    monkeypatch.delenv("BB_DESKTOP_TMAC")
    monkeypatch.setenv("PATH", os.path.dirname(env["BB_DESKTOP_TMAC"]))
    assert m.local_tmac() == env["BB_DESKTOP_TMAC"]
    # no terminal-mac here: the command goes to the mini, LAN first, relay second
    fake_ssh = tmp_path / "sshbin" / "ssh"
    fake_ssh.parent.mkdir()
    fake_ssh.write_text("#!/bin/sh\necho \"$@\" >> %s\ncase \"$*\" in *fia-run-on*) "
                        "echo '{\"leased\": true}'; echo __BBD_RC=0;; *) exit 255;; esac\n" % (tmp_path / "ssh.log"))
    fake_ssh.chmod(0o755)
    e = dict(env, BB_DESKTOP_TMAC="", PATH=str(fake_ssh.parent) + os.pathsep + "/usr/bin:/bin")
    rc, r, _ = run(e, "lease", "far-agent")
    assert rc == 0 and r["leased"] and r["ran_on"] == "mini via relay"
    tried = open(tmp_path / "ssh.log").read().splitlines()
    assert len(tried) == 2 and "192.168.1.84" in tried[0] and "fia-run-on.sh mini" in tried[1]
    assert calls(env) == []     # nothing ran against the local fake


def test_broker_client_desktop_lease_shells_out_and_releases(env, monkeypatch):
    for k in ("PATH", "FAKE_TMAC_LOG", "FAKE_TMAC_STATE", "BB_DESKTOP_STATE", "BB_DESKTOP_LOG",
              "BB_DESKTOP_SHOTS", "BB_DESKTOP_TMAC"):
        monkeypatch.setenv(k, env[k])
    import broker_client as bc
    with pytest.raises(RuntimeError):
        with bc.desktop_lease("ctx", purpose="ctx test") as d:
            d.click(1, 2)
            d.type("Hi")
            assert os.path.exists(d.screenshot())
            with pytest.raises(bc.DesktopBusy):
                with bc.desktop_lease("other"):
                    pass
            raise RuntimeError("boom")
    st = json.load(open(env["BB_DESKTOP_STATE"]))["default"]
    assert st["lease"] is None and st["history"][-1]["owner"] == "ctx"
    keys = [c[3] for c in calls(env) if c[:3] == ["action", "default", "key"]]
    assert keys == ["shift+h", "i"]


def vm_state(env):
    return open(env["FAKE_TMAC_STATE"]).read().strip()


def test_release_pauses_the_vm_and_lease_resumes_it(env):
    run(env, "lease", "a")
    rc, r, _ = run(env, "release", "a")
    assert rc == 0 and r["paused"] is True and vm_state(env) == "paused"
    rc, r, _ = run(env, "lease", "b")
    assert rc == 0 and vm_state(env) == "running"
    assert "paused (released by a)" in open(env["BB_DESKTOP_LOG"]).read()


def test_never_paused_with_a_viewer_attached(env):
    with open(env["BB_DESKTOP_PS_FILE"], "a") as f:
        f.write("/Users/x/.local/bin/terminal-mac view --protocol kitty default\n")
    run(env, "lease", "a")
    rc, r, _ = run(env, "release", "a")
    assert rc == 0 and r["paused"] == "viewer attached" and vm_state(env) == "running"
    assert ["pause", "default"] not in calls(env)
    # a viewer on another VM does not count
    open(env["BB_DESKTOP_PS_FILE"], "w").write("/Users/x/.local/bin/terminal-mac view other\n")
    rc, r, _ = run(env, "reap")
    assert vm_state(env) == "running"       # released just now: not idle yet
    e = dict(env, BB_DESKTOP_IDLE="0")
    rc, r, _ = run(e, "reap")
    assert r["reap"]["default"] is True and vm_state(env) == "paused"


def test_reap_pauses_only_idle_unleased_vms(env):
    e = dict(env, BB_DESKTOP_IDLE="1")
    # never seen before: gets the full idle window first
    rc, r, _ = run(e, "reap")
    assert rc == 0 and vm_state(env) == "running" and "idle" in r["reap"]["default"]
    # an active lease is never paused, however long it is
    run(e, "lease", "busy")
    time.sleep(1.2)
    rc, r, _ = run(e, "reap")
    assert r["reap"]["default"] == "leased by busy" and vm_state(env) == "running"
    run(e, "release", "busy")           # pauses
    run(e, "lease", "busy2")            # resumes
    run(e, "act", "busy2", "key", "a")
    st = json.load(open(env["BB_DESKTOP_STATE"]))
    st["default"]["lease"] = None       # as if released by hand without pausing
    json.dump(st, open(env["BB_DESKTOP_STATE"], "w"))
    rc, r, _ = run(e, "reap")
    assert vm_state(env) == "running"   # used under a second ago
    time.sleep(1.2)
    rc, r, _ = run(e, "reap")
    assert r["reap"]["default"] is True and vm_state(env) == "paused"
    clear_calls(env)
    rc, r, _ = run(e, "reap")
    assert r["reap"]["default"] == "already paused" and ["pause", "default"] not in calls(env)


def test_expired_lease_is_paused_by_reap(env):
    run(env, "lease", "a", "--ttl", "1")
    time.sleep(1.2)
    rc, r, _ = run(env, "reap")
    assert r["reap"]["default"] is True and vm_state(env) == "paused"
    assert "paused (lease expired)" in open(env["BB_DESKTOP_LOG"]).read()
