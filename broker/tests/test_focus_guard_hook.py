"""hooks-example-focus-guard.py: the Claude Code PreToolUse hook that turns
`open -a <browser>` and AppleScript `activate` into a polite `bb-show`.

It is run as a subprocess, exactly as Claude Code runs it (JSON on stdin,
decision JSON on stdout). It only reads a string and pattern-matches, so it
runs anywhere even though the commands it blocks are macOS ones."""
import json
import os
import subprocess
import sys

import pytest

HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "hooks-example-focus-guard.py")


def run_hook(payload, home):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    p = subprocess.run([sys.executable, HOOK], input=raw, capture_output=True, text=True,
                       env={**os.environ, "HOME": str(home)}, timeout=10)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else None


def bash(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


@pytest.mark.parametrize("cmd,rule", [
    ('open -a "Brave Browser" https://x.example', "open -a browser"),
    ("open -na Brave https://x.example", "open -a browser"),
    ("open -g -a 'Google Chrome' https://x", "open -a browser"),
    ("cd /tmp && open -a Safari index.html", "open -a browser"),
    ("open -a Chromium", "open -a browser"),
    ("""osascript -e 'tell application "Brave Browser" to activate'""", "applescript activate/tab"),
    ("""osascript -e 'tell application "Google Chrome"
  set active tab index of front window to 2
end tell'""", "applescript activate/tab"),
    ("""osascript -e 'tell application "Safari" to make new tab'""", "applescript activate/tab"),
    ("curl -s localhost:9222/json/activate/ABC", "raw cdp focus steal"),
    ("""websocat ws://x <<< '{"method":"Page.bringToFront"}'""", "raw cdp focus steal"),
])
def test_rude_commands_are_denied(cmd, rule, tmp_path):
    out = run_hook(bash(cmd), tmp_path)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "bb-show <url>" in hso["permissionDecisionReason"]
    audit = (tmp_path / ".claude" / "browser-focus-audit.log")
    if audit.exists():   # only written when ~/.claude exists
        assert json.loads(audit.read_text().splitlines()[-1])["rule"] == rule


def test_audit_log_written_when_claude_dir_exists(tmp_path):
    (tmp_path / ".claude").mkdir()
    run_hook(bash("open -a Safari https://x"), tmp_path)
    lines = (tmp_path / ".claude" / "browser-focus-audit.log").read_text().splitlines()
    assert json.loads(lines[-1]) == {"decision": "deny", "rule": "open -a browser"}


@pytest.mark.parametrize("cmd", [
    "bb-show https://x.example agent purpose",
    "curl -s -X POST localhost:9223/show -d '{}'",
    "open README.md",
    "open -a Preview report.pdf",
    "open -a TextEdit notes.txt",
    "ls -la",
    "",
])
def test_polite_and_unrelated_commands_pass(cmd, tmp_path):
    assert run_hook(bash(cmd), tmp_path) is None


def test_other_tools_are_ignored(tmp_path):
    assert run_hook({"tool_name": "Write", "tool_input": {"command": "open -a Safari"}}, tmp_path) is None


@pytest.mark.parametrize("raw", ["", "not json", "{}", '{"tool_name": "Bash"}',
                                 '{"tool_name": "Bash", "tool_input": null}'])
def test_garbage_input_never_blocks(raw, tmp_path):
    assert run_hook(raw, tmp_path) is None
