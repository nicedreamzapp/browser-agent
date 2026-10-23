#!/opt/homebrew/bin/python3.12
"""PreToolUse guard: no agent takes Matt's screen without taking its turn.

2026-09-20. Two Claude Code sessions ran `open -a "Brave Browser" <url>` four
minutes apart and threw his window back and forth between them. browser-broker
never saw it. The proxy swallows Target.activateTarget and Page.bringToFront,
but `open -a` and AppleScript `activate` are macOS calls, not CDP, so they walk
straight around it. There is no way to intercept them from inside the proxy.

So they get intercepted here instead, and redirected to `bb-show`, which asks
the broker for the screen and waits its turn. Same real Brave, same profile,
same logins, same result -- it just queues, and it closes the stale copy of the
page first so there is never a row of half-current duplicates.

This is a redirect, not a veto. Nothing an agent could do before is impossible
now; it just has to say excuse me.
"""
import json, os, re, sys

LOG = os.path.expanduser("~/.claude/browser-focus-audit.log")
BROWSERS = r"(brave(\s+browser)?|google\s+chrome|chromium|safari)"

# `open -a "Brave Browser" ...`  /  `open -na Brave ...`
OPEN_APP = re.compile(r"\bopen\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*a[a-zA-Z]*\s+[\"']?" + BROWSERS, re.I)
# osascript that fronts the browser or drives its tabs
OSA_FRONT = re.compile(
    r"tell\s+application\s+\"?" + BROWSERS + r"\"?[\s\S]{0,400}?"
    r"(to\s+activate|\bactivate\b|active\s+tab\s+index|make\s+new\s+tab|set\s+index)", re.I)
# the raw CDP back doors, in case something shells out to them
RAW_CDP = re.compile(r"/json/activate/|Target\.activateTarget|Page\.bringToFront", re.I)

ALREADY_POLITE = re.compile(r"\bbb-show\b|/show\b.*9223|9223.*\bshow\b", re.I)


def out(decision, reason, why):
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({"decision": decision, "rule": why}) + "\n")
    except OSError:
        pass
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if ev.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (ev.get("tool_input") or {}).get("command", "") or ""
    if not cmd or ALREADY_POLITE.search(cmd):
        sys.exit(0)

    for rx, why in ((OPEN_APP, "open -a browser"),
                    (OSA_FRONT, "applescript activate/tab"),
                    (RAW_CDP, "raw cdp focus steal")):
        if rx.search(cmd):
            out("deny",
                "BLOCKED: this takes Matt's screen without taking a turn, and it goes around\n"
                "browser-broker (macOS calls are not CDP, so the proxy cannot queue them).\n"
                "Two sessions doing this minutes apart is what throws his window around.\n\n"
                "Use this instead, same Brave, same logins, it just waits its turn:\n"
                "    bb-show <url> [owner] [purpose]\n\n"
                "Exit 0 means shown. Exit 2 means another agent has the screen right now --\n"
                "when that happens, give Matt the link in your reply and move on. Do not force it.\n"
                "Who holds it: curl -s localhost:9223/stage",
                why)
    sys.exit(0)


if __name__ == "__main__":
    main()
