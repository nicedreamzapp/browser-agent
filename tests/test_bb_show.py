"""bb-show: the shell command agents use instead of `open -a`.

Runs the real script with bash and curl against a scripted broker. The
broker-down fallback calls macOS `open`; here a stand-in `open` on PATH
records the call, so the fallback logic is tested on any OS."""
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BB_SHOW = os.path.join(ROOT, "bb-show")

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("curl")),
                                reason="bb-show needs bash and curl")


class Broker(BaseHTTPRequestHandler):
    code = 200
    reply = {}
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Broker.seen.append((self.path, json.loads(self.rfile.read(n))))
        raw = json.dumps(Broker.reply).encode()
        self.send_response(Broker.code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def broker_url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Broker)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    Broker.code, Broker.reply, Broker.seen = 200, {}, []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def fake_open(tmp_path):
    """A stand-in for macOS `open` that records its arguments."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "open.log"
    script = bindir / "open"
    script.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit "${{FAKE_OPEN_EXIT:-0}}"\n')
    script.chmod(0o755)
    # python3 must still be found: bb-show builds its JSON body with it.
    pydir = tmp_path / "py"
    pydir.mkdir()
    (pydir / "python3").symlink_to(sys.executable)
    return bindir, pydir, log


def run(args, env_extra, fake_open):
    bindir, pydir, _ = fake_open
    env = {"PATH": f"{bindir}:{pydir}:/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}
    env.update(env_extra)
    return subprocess.run(["bash", BB_SHOW, *args], capture_output=True, text=True,
                          env=env, timeout=30)


def test_usage(fake_open):
    p = run([], {}, fake_open)
    assert p.returncode == 1 and "usage: bb-show" in p.stderr


def test_shown(broker_url, fake_open):
    p = run(["https://x.example/a b?q=\"1\"", "agent-7", "show the report"],
            {"BROKER_URL": broker_url}, fake_open)
    assert p.returncode == 0, p.stderr
    out = p.stdout.strip().splitlines()
    assert out[0] == 'shown: https://x.example/a b?q="1"'
    assert out[1].startswith("when the task is done, close it: bb-close agent-7")
    assert Broker.seen == [("/show", {"url": 'https://x.example/a b?q="1"', "owner": "agent-7",
                                      "purpose": "show the report"})]
    assert not fake_open[2].exists()


def test_owner_defaults(broker_url, fake_open):
    run(["https://x"], {"BROKER_URL": broker_url, "BROKER_OWNER": "from-env"}, fake_open)
    run(["https://x"], {"BROKER_URL": broker_url, "CLAUDE_SESSION_ID": "sess-123"}, fake_open)
    run(["https://x"], {"BROKER_URL": broker_url}, fake_open)
    assert [b["owner"] for _, b in Broker.seen] == ["from-env", "sess-123", "agent"]
    assert all(b["purpose"] is None for _, b in Broker.seen)


def test_busy_exits_2_and_does_not_force(broker_url, fake_open):
    Broker.code = 409
    Broker.reply = {"held_by": "other", "seconds_left": 12}
    p = run(["https://x"], {"BROKER_URL": broker_url}, fake_open)
    assert p.returncode == 2
    assert "screen busy" in p.stderr and "not forcing it" in p.stderr
    assert not fake_open[2].exists()


def test_broker_down_falls_back_to_open(fake_open):
    p = run(["https://x"], {"BROKER_URL": "http://127.0.0.1:9", "BROWSER_APP": "Google Chrome"},
            fake_open)
    assert p.returncode == 0
    assert "falling back" in p.stderr
    assert fake_open[2].read_text().strip() == "-a Google Chrome https://x"


def test_broker_down_and_open_fails(fake_open):
    p = run(["https://x"], {"BROKER_URL": "http://127.0.0.1:9", "FAKE_OPEN_EXIT": "1"}, fake_open)
    assert p.returncode == 1


def test_unexpected_status_falls_back(broker_url, fake_open):
    Broker.code = 500
    p = run(["https://x"], {"BROKER_URL": broker_url}, fake_open)
    assert p.returncode == 0 and "code=500" in p.stderr
    assert fake_open[2].read_text().strip() == "-a Brave Browser https://x"


BB_CLOSE = os.path.join(ROOT, "bb-close")


def run_close(args, env_extra, fake_open):
    bindir, pydir, _ = fake_open
    env = {"PATH": f"{bindir}:{pydir}:/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}
    env.update(env_extra)
    return subprocess.run(["bash", BB_CLOSE, *args], capture_output=True, text=True,
                          env=env, timeout=30)


def test_close_usage(fake_open):
    p = run_close([], {}, fake_open)
    assert p.returncode == 1 and "usage: bb-close" in p.stderr


def test_close_posts_unshow(broker_url, fake_open):
    Broker.reply = {"closed": [{"owner": "agent-7", "target": "abc", "url": "https://x/a"}],
                    "left_open": []}
    p = run_close(["agent-7"], {"BROKER_URL": broker_url}, fake_open)
    assert p.returncode == 0, p.stderr
    assert Broker.seen == [("/unshow", {"match": "agent-7"})]
    assert "closed: https://x/a (agent-7)" in p.stdout


def test_close_nothing_matched_exits_3(broker_url, fake_open):
    Broker.reply = {"closed": [], "left_open": []}
    p = run_close(["nope"], {"BROKER_URL": broker_url}, fake_open)
    assert p.returncode == 3 and "nothing to close" in p.stderr


def test_close_broker_down_never_opens_or_guesses(fake_open):
    p = run_close(["x"], {"BROKER_URL": "http://127.0.0.1:9"}, fake_open)
    assert p.returncode == 1 and "nothing closed" in p.stderr
    assert not fake_open[2].exists()
