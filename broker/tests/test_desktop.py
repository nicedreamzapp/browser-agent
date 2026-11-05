"""bb-desktop: the desktop lease on the virtual Mac, against a fake terminal-mac on PATH."""
import base64, fcntl, importlib.machinery, importlib.util, json, os, stat, subprocess, sys, time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bb-desktop")

FAKE_TMAC = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
with open(os.environ["FAKE_TMAC_LOG"], "a") as f:
    f.write(json.dumps(a) + "\n")
vm_state = open(os.environ["FAKE_TMAC_STATE"]).read().strip()
suspend_ok = os.environ.get("FAKE_TMAC_SUSPEND") == "1"
if a[0] == "--help":
    print("    status|pause|resume%s [NAME]   Manage the VM" % ("|suspend" if suspend_ok else ""))
    print("    action [NAME] status"); sys.exit(0)
if a[0] in ("suspend", "restore") and not suspend_ok:
    print("terminal-mac: Unknown command: " + a[0], file=sys.stderr); sys.exit(1)
if a[0] == "suspend":
    import time; time.sleep(float(os.environ.get("FAKE_TMAC_SUSPEND_SLEEP", "0")))
    if os.environ.get("FAKE_TMAC_SUSPEND_FAIL"):
        print("terminal-mac: could not save the VM state", file=sys.stderr); sys.exit(1)
    open(os.environ["FAKE_TMAC_STATE"], "w").write("stopped"); print("{}"); sys.exit(0)
if a[0] == "restore":
    if os.environ.get("FAKE_TMAC_RESTORE_ERR"):
        print("terminal-mac: " + os.environ["FAKE_TMAC_RESTORE_ERR"], file=sys.stderr); sys.exit(1)
    open(os.environ["FAKE_TMAC_STATE"], "w").write("running"); print("{}"); sys.exit(0)
if a[0] == "start":
    open(os.environ["FAKE_TMAC_STATE"], "w").write("running"); print("{}"); sys.exit(0)
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
    # The real CLI's option parser (CLI.swift `action`): anything else is "Unknown option".
    flags, opts = {"double"}, {"x", "y", "x2", "y2", "dx", "dy", "text", "key", "button", "count", "output", "timeout"}
    rest = a[3:]
    while rest:
        t = rest.pop(0)
        if t == "--":
            break
        if t.startswith("--") and len(t) > 2:
            if t[2:] in flags:
                continue
            if t[2:] in opts and rest:
                rest.pop(0)
                continue
            print("terminal-mac: Unknown option: " + t, file=sys.stderr); sys.exit(1)
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
             BB_DESKTOP_PS_FILE=str(tmp_path / "ps.txt"), BB_DESKTOP_CPU="0",
             BB_DESKTOP_CONFIG=str(tmp_path / "no-config.env"), TERMINAL_MAC_HOME=str(tmp_path / "tmhome"))
    for k in ("BB_DESKTOP_MINI", "BB_DESKTOP_RELAY", "BB_DESKTOP_RELAY_RUN", "BB_DESKTOP_SUSPEND",
              "BB_DESKTOP_REQUEST_ID", "BB_DESKTOP_IDLE", "BB_DESKTOP_LOCK_WAIT"):
        e.pop(k, None)
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
    run(env, "lease", "a", "--ttl", "5")
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
    fake_ssh.write_text("#!/bin/sh\necho \"$@\" >> %s\ncase \"$*\" in *run-on.sh*) "
                        "echo __BBD_START; echo '{\"leased\": true}'; echo __BBD_RC=0;; *) exit 255;; esac\n"
                        % (tmp_path / "ssh.log"))
    fake_ssh.chmod(0o755)
    e = remote_env(env, fake_ssh)
    rc, r, _ = run(e, "lease", "far-agent")
    assert rc == 0 and r["leased"] and r["ran_on"] == "VM host via relay"
    tried = open(tmp_path / "ssh.log").read().splitlines()
    assert len(tried) == 2 and "user@vm-host.lan" in tried[0] and "run-on.sh vm-host" in tried[1]
    assert "BB_DESKTOP_REQUEST_ID=" in base64.b64decode(tried[1].split("echo ")[1].split(" |")[0]).decode()
    assert calls(env) == []     # nothing ran against the local fake


def remote_env(env, fake_ssh):
    return dict(env, BB_DESKTOP_TMAC="", PATH=str(fake_ssh.parent) + os.pathsep + "/usr/bin:/bin",
                BB_DESKTOP_MINI="user@vm-host.lan", BB_DESKTOP_RELAY="relay@relay.example",
                BB_DESKTOP_RELAY_RUN="/opt/run-on.sh vm-host")


def test_broker_client_desktop_lease_shells_out_and_releases(env, monkeypatch):
    for k in ("PATH", "FAKE_TMAC_LOG", "FAKE_TMAC_STATE", "BB_DESKTOP_STATE", "BB_DESKTOP_LOG",
              "BB_DESKTOP_SHOTS", "BB_DESKTOP_TMAC", "BB_DESKTOP_CONFIG", "BB_DESKTOP_CPU", "BB_DESKTOP_PS_FILE"):
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


def test_suspend_when_terminal_mac_has_it_and_lease_restores(env):
    e = dict(env, FAKE_TMAC_SUSPEND="1")
    run(e, "lease", "a")
    rc, r, _ = run(e, "release", "a")
    assert rc == 0 and r["paused"] is True
    assert calls(env).count(["--help"]) == 1
    assert ["suspend", "default"] in calls(env) and ["pause", "default"] not in calls(env)
    assert vm_state(env) == "stopped"       # a suspended VM exits, so it reads as stopped
    assert "suspended (released by a)" in open(env["BB_DESKTOP_LOG"]).read()
    clear_calls(env)
    rc, r, _ = run(e, "lease", "b")
    assert rc == 0 and vm_state(env) == "running"
    c = calls(env)
    assert c.index(["restore", "default"]) < c.index(["action", "default", "status"])
    assert not json.load(open(env["BB_DESKTOP_STATE"]))["default"].get("suspended")
    # the capability is read from --help once per binary, then cached
    run(e, "release", "b")
    run(e, "lease", "c")
    run(e, "release", "c")
    assert sum(1 for x in calls(env) if x == ["--help"]) == 0


def test_pause_fallback_without_suspend_and_stopped_vm_still_refused(env):
    run(env, "lease", "a")
    run(env, "release", "a")
    assert ["--help"] in calls(env)
    assert ["pause", "default"] in calls(env) and ["suspend", "default"] not in calls(env)
    assert vm_state(env) == "paused"
    assert run(env, "lease", "b")[0] == 0 and vm_state(env) == "running"
    run(env, "release", "b")
    # a VM we did not suspend that is stopped is not ours to start
    open(env["FAKE_TMAC_STATE"], "w").write("stopped")
    rc, r, _ = run(env, "lease", "c")
    assert rc == 2 and r["vm_state"] == "stopped" and ["restore", "default"] not in calls(env)


def test_reap_and_expiry_suspend_too(env):
    e = dict(env, FAKE_TMAC_SUSPEND="1")
    run(e, "lease", "a", "--ttl", "1")
    time.sleep(1.2)
    rc, r, _ = run(e, "reap")
    assert r["reap"]["default"] is True and ["suspend", "default"] in calls(env)
    rc, r, _ = run(dict(e, BB_DESKTOP_IDLE="0"), "reap")
    assert r["reap"]["default"] == "already stopped"



# ---- review fixes (2026-11-04) -------------------------------------------------------
def state_of(env):
    return json.load(open(env["BB_DESKTOP_STATE"]))["default"]


def edit_state(env, **fields):
    st = json.load(open(env["BB_DESKTOP_STATE"]))
    st["default"].update(fields)
    json.dump(st, open(env["BB_DESKTOP_STATE"], "w"))


def test_a_repeated_request_id_runs_once_and_returns_the_first_result(env):
    run(env, "lease", "a")
    clear_calls(env)
    e = dict(env, BB_DESKTOP_REQUEST_ID="req-1")
    rc1, r1, _ = run(e, "act", "a", "type", "hi")
    rc2, r2, _ = run(e, "act", "a", "type", "hi")
    assert rc1 == rc2 == 0 and r2.get("repeat") and r2["typed_chars"] == 2
    assert [c[3] for c in calls(env) if c[2:3] == ["key"]] == ["h", "i"]      # typed once, not twice
    # a refused request is remembered too
    rc, r, _ = run(dict(env, BB_DESKTOP_REQUEST_ID="req-2"), "act", "nobody", "key", "a")
    rc2, r2, _ = run(dict(env, BB_DESKTOP_REQUEST_ID="req-2"), "act", "nobody", "key", "a")
    assert rc == rc2 == 3 and r2.get("repeat")


def test_lan_run_that_started_is_never_resent_over_the_relay(env, tmp_path):
    fake_ssh = tmp_path / "sshbin" / "ssh"
    fake_ssh.parent.mkdir()
    # the LAN connects, the command starts, then the answer is lost
    fake_ssh.write_text("#!/bin/sh\necho \"$@\" >> %s\ncase \"$*\" in *run-on.sh*) echo __BBD_START;"
                        " echo '{}'; echo __BBD_RC=0;; *) echo __BBD_START;; esac\n" % (tmp_path / "ssh.log"))
    fake_ssh.chmod(0o755)
    rc, r, err = run(remote_env(env, fake_ssh), "act", "a", "type", "hello")
    assert rc == 1 and "not re-sent" in err
    tried = open(tmp_path / "ssh.log").read().splitlines()
    assert len(tried) == 1 and "vm-host.lan" in tried[0]
    # only a failed connection (ssh exit 255, nothing started) goes to the relay
    fake_ssh.write_text("#!/bin/sh\necho \"$@\" >> %s\ncase \"$*\" in *run-on.sh*) echo __BBD_START;"
                        " echo '{\"ok\": 1}'; echo __BBD_RC=0;; *) exit 255;; esac\n" % (tmp_path / "ssh.log"))
    rc, r, _ = run(remote_env(env, fake_ssh), "peek")
    assert rc == 0 and r["ran_on"] == "VM host via relay"


def test_state_lock_waits_a_bounded_time(env):
    run(env, "lease", "a")
    with open(env["BB_DESKTOP_STATE"] + ".lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        t = time.time()
        rc, r, err = run(dict(env, BB_DESKTOP_LOCK_WAIT="0.5"), "act", "a", "key", "a")
        assert rc == 1 and "locked" in err and time.time() - t < 10


def test_suspend_runs_outside_the_lock_with_the_flag_set_first(env):
    e = dict(env, FAKE_TMAC_SUSPEND="1", FAKE_TMAC_SUSPEND_SLEEP="2")
    run(e, "lease", "a")
    p = subprocess.Popen([sys.executable, BIN, "release", "a"], env=e, stdout=subprocess.PIPE, text=True)
    time.sleep(1.0)
    rc, r, _ = run(dict(e, BB_DESKTOP_LOCK_WAIT="0.5"), "peek")     # the lock is free mid-suspend
    assert rc == 0 and r.get("suspending")
    mid = state_of(env)
    assert mid["suspended"] is True and mid["suspending"]
    # a lease that arrives mid-suspend waits for it to finish, then restores the VM
    clear_calls(env)
    rc, r, _ = run(e, "lease", "b")
    assert rc == 0 and vm_state(env) == "running"
    assert p.wait(20) == 0
    c = calls(env)
    assert ["restore", "default"] in c
    assert "suspending" not in state_of(env) and not state_of(env).get("suspended")


def test_act_refused_while_suspending_and_stale_mark_ignored(env):
    run(env, "lease", "a")
    edit_state(env, suspending={"since": time.time(), "how": "suspend", "pid": os.getpid()})
    rc, r, err = run(env, "act", "a", "key", "a")
    assert rc == 2 and "being suspended" in err
    edit_state(env, suspending={"since": time.time() - 3600, "how": "suspend", "pid": os.getpid()})
    assert run(env, "act", "a", "key", "a")[0] == 0
    # E: a mark whose process is gone is stale straight away
    dead = subprocess.Popen(["true"]); dead.wait()
    edit_state(env, suspending={"since": time.time(), "how": "suspend", "pid": dead.pid})
    assert run(env, "act", "a", "key", "a")[0] == 0


def test_a_failed_suspend_clears_the_flag(env):
    e = dict(env, FAKE_TMAC_SUSPEND="1", FAKE_TMAC_SUSPEND_FAIL="1")
    run(e, "lease", "a")
    rc, r, _ = run(e, "release", "a")
    assert rc == 0 and str(r["paused"]).startswith("pause failed")
    st = state_of(env)
    assert "suspended" not in st and "suspending" not in st
    assert "suspend FAILED" in open(env["BB_DESKTOP_LOG"]).read()


def test_timeouts_cover_the_slowest_path():
    m = load_module()
    assert m.budget(["lease", "a"]) >= 180 + 120 + 60
    assert m.budget(["release", "a"]) >= 30 + 180
    assert m.budget(["act", "a", "type"] + ["x"] * 100) > 120
    import broker_client as bc
    for argv in (["lease", "a"], ["release", "a"], ["act", "a", "type", "--text", "y" * 300], ["peek"]):
        assert bc._desktop_timeout(argv) > m.budget(argv) + 30     # host budget + remote slack


def test_stale_suspended_flag_never_boots_the_vm(env):
    e = dict(env, FAKE_TMAC_SUSPEND="1")
    assert run(e, "lease", "a")[0] == 0
    run(e, "release", "a")
    assert state_of(env)["suspended"] is True
    open(env["FAKE_TMAC_STATE"], "w").write("running")      # woken outside the broker
    clear_calls(env)
    assert run(e, "lease", "b")[0] == 0
    assert "suspended" not in state_of(env)                 # cleared when status read running
    assert ["restore", "default"] not in calls(env) and ["start", "default"] not in calls(env)


def test_restore_failure_refuses_unless_there_is_no_saved_state(env):
    e = dict(env, FAKE_TMAC_SUSPEND="1")
    run(e, "lease", "a")
    run(e, "release", "a")
    clear_calls(env)
    rc, r, err = run(dict(e, FAKE_TMAC_RESTORE_ERR="the disk image is busy"), "lease", "b")
    assert rc == 2 and "ask the human" in err and ["start", "default"] not in calls(env)
    # C: stopped with only our flag never falls back to start, even on "no saved state"
    rc, r, err = run(dict(e, FAKE_TMAC_RESTORE_ERR="No saved state for default"), "lease", "c")
    assert rc == 2 and "ask the human" in err and ["start", "default"] not in calls(env)
    # ... only terminal-mac's own "suspended" state does
    open(env["FAKE_TMAC_STATE"], "w").write("suspended")
    rc, r, _ = run(dict(e, FAKE_TMAC_RESTORE_ERR="No saved state for default"), "lease", "d")
    assert rc == 0 and ["start", "default"] in calls(env) and vm_state(env) == "running"


def test_reap_gives_a_vm_it_did_not_wake_a_fresh_10_minutes(env):
    # running, no lease, an old last_used from long ago: Matt started it over Screen Sharing
    run(env, "lease", "a")
    run(env, "release", "a")           # pauses it (fake pause) and clears "ours"
    open(env["FAKE_TMAC_STATE"], "w").write("running")
    edit_state(env, last_used=time.time() - 7200)
    e = dict(env, BB_DESKTOP_IDLE="1")
    rc, r, _ = run(e, "reap")
    assert r["reap"]["default"] == "first seen running; idle clock started" and vm_state(env) == "running"
    rc, r, _ = run(e, "reap")
    assert vm_state(env) == "running"   # under a second since first sight
    time.sleep(1.2)
    rc, r, _ = run(dict(e, BB_DESKTOP_CPU="45"), "reap")
    assert r["reap"]["default"].startswith("busy") and vm_state(env) == "running"
    rc, r, _ = run(e, "reap")
    assert r["reap"]["default"] is True and vm_state(env) == "paused"


def test_fetch_fails_loudly(env, monkeypatch):
    m = load_module()
    png = base64.b64encode(b"\x89PNG data").decode()
    out = {"v": ""}
    monkeypatch.setattr(m, "run_on_mini", lambda script, timeout, retry_after_start=False: (out["v"], "lan"))
    dest = os.path.join(env["tmp"], "f.png")
    out["v"] = "__BBD_START\n%s\n\n__BBD_RC=0\n" % png
    m.fetch("/tmp/x.png", dest)
    assert open(dest, "rb").read() == b"\x89PNG data"
    for bad in ("__BBD_START\n\n__BBD_RC=1\n", "__BBD_START\n%s!!\n__BBD_RC=0\n" % png[:-2],
                "__BBD_START\n%s\n" % png, "__BBD_START\n%s\n__BBD_RC=0\n" % base64.b64encode(b"nope").decode()):
        out["v"] = bad
        with pytest.raises(m.Exit):
            m.fetch("/tmp/x.png", dest)


def test_host_addresses_come_from_config_not_the_repo(tmp_path, monkeypatch):
    cfg = tmp_path / "desktop.env"
    cfg.write_text("# comment\nBB_DESKTOP_MINI=user@from-file\nBB_DESKTOP_RELAY='relay@from-file'\n")
    m = load_module()
    monkeypatch.delenv("BB_DESKTOP_MINI", raising=False)
    monkeypatch.setenv("BB_DESKTOP_RELAY", "relay@from-env")
    m._load_config(str(cfg))
    assert os.environ["BB_DESKTOP_MINI"] == "user@from-file"
    assert os.environ["BB_DESKTOP_RELAY"] == "relay@from-env"      # the environment wins
    monkeypatch.delenv("BB_DESKTOP_MINI")
    import re as _re
    for f in (BIN, __file__, os.path.join(ROOT, "README.md")):
        text = open(f).read()
        assert not _re.search(r"192\.168\.\d|10\.\d+\.\d+\.\d+|\broot@\d", text), f


def test_unquoted_words_are_all_typed(env):
    run(env, "lease", "a")
    clear_calls(env)
    rc, r, _ = run(env, "act", "a", "type", "hi", "you")
    assert rc == 0 and r["typed_chars"] == 6
    assert [c[3] for c in calls(env)] == ["h", "i", "space", "y", "o", "u"]


# ---- round 2 review fixes ------------------------------------------------------------
def test_A_failed_suspend_keeps_the_flag_unless_the_vm_is_still_running(env, tmp_path):
    # the suspend fails but the VM went down anyway (it may have saved): the flag stays
    fake = tmp_path / "bin" / "terminal-mac"
    src = fake.read_text().replace('''    if os.environ.get("FAKE_TMAC_SUSPEND_FAIL"):''', '''    if os.environ.get("FAKE_TMAC_SUSPEND_FAIL") == "down":
        open(os.environ["FAKE_TMAC_STATE"], "w").write("stopped"); sys.exit(1)
    if os.environ.get("FAKE_TMAC_SUSPEND_FAIL"):''')
    fake.write_text(src)
    e = dict(env, FAKE_TMAC_SUSPEND="1", FAKE_TMAC_SUSPEND_FAIL="down")
    run(e, "lease", "a")
    rc, r, _ = run(e, "release", "a")
    assert str(r["paused"]).startswith("pause failed")
    assert state_of(env)["suspended"] is True and "suspending" not in state_of(env)


def test_B_lease_budget_and_a_lease_that_finished_after_the_caller_gave_up_is_reused(env):
    m = load_module()
    assert m.budget(["lease", "a"]) >= 300 + 180 + 120 + 60 + 60
    import broker_client as bc
    assert bc._desktop_timeout(["lease", "a"]) > m.budget(["lease", "a"]) + 30
    # the caller timed out, but the lease landed on the host: the same owner just gets it back
    assert run(env, "lease", "slow")[0] == 0
    for _ in range(3):
        rc, r, _ = run(env, "lease", "slow")
        assert rc == 0 and r.get("already_yours")
    assert not state_of(env)["history"]         # nothing counted toward the stuck rule


def test_B_check_ready_respects_the_deadline(env, monkeypatch):
    m = load_module()
    with pytest.raises(m.Exit) as e:
        m.left(time.time() + 1, 60)
    assert e.value.code == 2 and "reused" in e.value.msg
    assert 5 <= m.left(time.time() + 40, 60) <= 40


def test_D_cpu_counts_only_the_process_holding_this_vms_disk(monkeypatch):
    m = load_module()
    monkeypatch.delenv("BB_DESKTOP_CPU", raising=False)
    seen = []

    class P:
        def __init__(self, out): self.stdout = out

    def fake_run(cmd, **kw):
        seen.append(cmd)
        if cmd[0].endswith("lsof"):
            assert cmd[-1].endswith("/machines/default/disk.img")
            return P("4435\n")
        if cmd[:3] == ["/bin/ps", "-axo", "pid=,command="]:
            return P("4361 /x/terminal-mac __run default\n4435 /System/.../com.apple.Virtualization.VirtualMachine\n"
                     "5000 /System/.../com.apple.Virtualization.VirtualMachine\n")
        assert cmd[-1] == "4435"            # the other VM's process is not sampled
        return P("30.0\n")
    monkeypatch.setattr(m.subprocess, "run", fake_run)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    assert m.vm_cpu("default") == 30.0


def test_F_done_set_dead_runner_and_unrecordable_result(env, monkeypatch, tmp_path):
    m = load_module()
    monkeypatch.setattr(m, "STATE", env["BB_DESKTOP_STATE"])
    monkeypatch.setattr(m, "LOG", env["BB_DESKTOP_LOG"])
    dead = subprocess.Popen(["true"]); dead.wait()
    with m.State() as st:
        st.data["_requests"] = {"r1": {"t": time.time(), "state": "running", "pid": dead.pid}}
        st.save()
    rc, reply = m.request_claim("r1", 5)
    assert rc == 1 and "died" in reply["error"]
    assert m.request_claim("r2", 5) is None
    assert json.load(open(env["BB_DESKTOP_STATE"]))["_requests"]["r2"]["pid"] == os.getpid()
    # recording keeps failing: a warning in the log, never a failed action
    calls_ = []

    class Broken:
        def __enter__(self):
            calls_.append(1)
            raise m.Exit(1, "locked")

        def __exit__(self, *a):
            pass
    monkeypatch.setattr(m, "State", Broken)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    assert m.request_done("r2", 0, {"ok": 1}) is False and len(calls_) == 5
    assert "WARNING request r2 finished" in open(env["BB_DESKTOP_LOG"]).read()


def test_G1_reap_leaves_a_vm_whose_mailbox_was_used_outside_a_lease(env):
    run(env, "lease", "a", "--ttl", "1")
    ex = os.path.join(env["TERMINAL_MAC_HOME"], "machines", "default", "exchange")
    os.makedirs(ex)
    open(os.path.join(ex, "request-x.json"), "w").close()      # a raw terminal-mac action just now
    time.sleep(1.2)
    rc, r, _ = run(env, "reap")
    assert r["reap"]["default"].startswith("mailbox active") and vm_state(env) == "running"
    old = time.time() - 3600
    for f in [ex] + [os.path.join(ex, n) for n in os.listdir(ex)]:
        os.utime(f, (old, old))
    time.sleep(1.2)
    rc, r, _ = run(dict(env, BB_DESKTOP_IDLE="1"), "reap")
    assert r["reap"]["default"] is True and vm_state(env) == "paused"


def mcp_session(env, *msgs, idle=None):
    e = dict(env, **({"BB_DESKTOP_MCP_IDLE": str(idle)} if idle else {}))
    p = subprocess.Popen([sys.executable, BIN, "mcp"], env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    return p


def mcp_call(p, mid, method, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())


def test_G2_mcp_serves_terminal_macs_tools_behind_a_lease(env):
    p = mcp_session(env)
    try:
        init = mcp_call(p, 1, "initialize", {"protocolVersion": "2025-06-18"})
        assert init["result"]["serverInfo"]["name"] == "terminal-mac"
        names = [t["name"] for t in mcp_call(p, 2, "tools/list")["result"]["tools"]]
        assert names == ["screenshot", "click", "move", "drag", "scroll", "type", "key", "status"]
        r = mcp_call(p, 3, "tools/call", {"name": "status", "arguments": {}})
        assert "lease: free" in r["result"]["content"][0]["text"]       # status takes no lease
        clear_calls(env)
        r = mcp_call(p, 4, "tools/call", {"name": "type", "arguments": {"text": "Hi"}})
        assert r["result"]["content"][0]["text"] == "Done: type."
        lease = state_of(env)["lease"]
        assert lease["owner"].startswith("mcp-")
        assert [c[3] for c in calls(env) if c[2:3] == ["key"]] == ["shift+h", "i"]     # per key
        mcp_call(p, 5, "tools/call", {"name": "click", "arguments": {"x": 5, "y": 6, "clicks": 2}})
        assert calls(env)[-1] == ["action", "default", "click", "5", "6", "--double"]
        mcp_call(p, 51, "tools/call", {"name": "click", "arguments": {"x": 5, "y": 6, "clicks": 3, "button": "right"}})
        assert calls(env)[-2:] == [["action", "default", "click", "5", "6", "--button", "right", "--double"],
                                   ["action", "default", "click", "5", "6", "--button", "right", "--count", "3"]]
        r = mcp_call(p, 6, "tools/call", {"name": "screenshot", "arguments": {}})
        assert r["result"]["content"][0]["type"] == "image" and r["result"]["content"][0]["data"]
        assert state_of(env)["lease"]["expires"] >= lease["expires"]       # renewed by each call
        # another agent is refused while the MCP session holds it
        assert run(env, "lease", "other")[0] == 4
    finally:
        p.stdin.close()
        p.wait(20)
    assert state_of(env)["lease"] is None                   # released on exit
    assert vm_state(env) == "paused"


def test_G2_mcp_releases_after_idle_and_reports_a_busy_desktop(env):
    run(env, "lease", "someone", "--purpose", "app test")
    p = mcp_session(env, idle=1)
    try:
        r = mcp_call(p, 1, "tools/call", {"name": "key", "arguments": {"key": "a"}})
        assert r["result"]["isError"] and "someone" in r["result"]["content"][0]["text"]
        run(env, "release", "someone")
        r = mcp_call(p, 2, "tools/call", {"name": "key", "arguments": {"key": "a"}})
        assert not r["result"].get("isError") and state_of(env)["lease"]["owner"].startswith("mcp-")
        deadline = time.time() + 10
        while state_of(env)["lease"] is not None and time.time() < deadline:
            time.sleep(0.2)
        assert state_of(env)["lease"] is None               # idle release
    finally:
        p.stdin.close()
        p.wait(20)



# ---- round 3: the MCP server ------------------------------------------------------------
def test_fake_terminal_mac_rejects_unknown_options_like_the_real_one(env):
    run(env, "lease", "a")
    rc, _, err = run(env, "act", "a", "click", "1", "2", "--triple")
    assert rc == 1 and "Unknown option: --triple" in err


def test_mcp_sigterm_releases_and_a_killed_server_leaves_a_free_lease(env):
    import signal
    p = mcp_session(env, idle=300)
    mcp_call(p, 1, "tools/call", {"name": "key", "arguments": {"key": "a"}})
    lease = state_of(env)["lease"]
    assert lease["owner"] == "mcp-%d" % p.pid and lease["ttl"] == 360         # MCP_IDLE + 60
    p.send_signal(signal.SIGTERM)
    assert p.wait(20) == 0
    assert state_of(env)["lease"] is None
    # kill -9: nothing runs, but a lease held by a dead mcp-<pid> on this host is free
    p = mcp_session(env, idle=300)
    mcp_call(p, 1, "tools/call", {"name": "key", "arguments": {"key": "a"}})
    p.kill()
    p.wait(20)
    assert state_of(env)["lease"]["owner"] == "mcp-%d" % p.pid
    rc, r, _ = run(env, "lease", "next-agent")
    assert rc == 0 and r["owner"] == "next-agent"
    assert "its MCP server is gone" in open(env["BB_DESKTOP_LOG"]).read()


def test_mcp_survives_garbage(env):
    p = mcp_session(dict(env, BB_DESKTOP_SHOTS="/dev/null/nope"))
    try:
        def raw(line):
            p.stdin.write(line + "\n")
            p.stdin.flush()
            return json.loads(p.stdout.readline())
        assert raw("not json")["error"]["code"] == -32700
        assert raw("[1, 2]")["error"]["code"] == -32600
        assert raw('"text"')["error"]["code"] == -32600
        assert "result" in raw('{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": 7}')
        bad = [{"name": "click", "arguments": {"x": "abc", "y": 1}},
               {"name": "click", "arguments": [1, 2]},
               {"name": "click", "arguments": {"x": True, "y": 1}},
               {"name": "type", "arguments": "hello"},
               {"name": 5},
               {"name": "screenshot", "arguments": {}}]           # OSError: the shots dir cannot exist
        for i, params in enumerate(bad):
            r = mcp_call(p, 10 + i, "tools/call", params)
            assert r["result"]["isError"], params
        r = mcp_call(p, 99, "tools/call", {"name": "status", "arguments": {}})
        assert not r["result"].get("isError") and "is running" in r["result"]["content"][0]["text"]
        assert p.poll() is None
    finally:
        p.stdin.close()
        p.wait(20)


def test_mcp_idle_clock_starts_when_a_call_ends(env, monkeypatch):
    m = load_module()
    sess = m.McpSession()
    monkeypatch.setattr(sess, "_call", lambda tool, a, need: time.sleep(0.5) or [])
    t = time.time()
    sess.call("key", {"key": "a"})
    assert sess.last >= t + 0.5


def test_mcp_bad_arguments_never_take_the_lease(env):
    p = mcp_session(env)
    try:
        for i, params in enumerate([{"name": "click", "arguments": {"x": "abc", "y": 1}},
                                    {"name": "type", "arguments": {"text": "caf\u00e9"}},
                                    {"name": "drag", "arguments": {"x": 1, "y": 2}}]):
            assert mcp_call(p, i, "tools/call", params)["result"]["isError"]
        assert not os.path.exists(env["BB_DESKTOP_STATE"]) or state_of(env)["lease"] is None
    finally:
        p.stdin.close()
        p.wait(20)
