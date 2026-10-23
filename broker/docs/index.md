---
title: browser-broker, one logged-in browser shared by many AI agents
description: >-
  browser-broker is a local Python proxy on Chrome DevTools port 9222. Each AI
  agent gets its own private tab in your real Chrome or Brave, with your logins,
  and cannot see or steal other agents' tabs or yours.
---

# browser-broker

**Your second agent just stole the tab your first one was using.**

browser-broker is a local proxy that sits on Chrome's debug port (9222) and gives
each AI agent its own private tab in your real, logged-in browser. Agents cannot
see or take each other's tabs, and they cannot see yours.

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/get.sh | bash
```

Your tools keep dialing port 9222 exactly as they do today. Nothing in them changes.

## The problem it fixes

The [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/)
(CDP) has no idea of ownership. Every client connected to port 9222 sees every
tab, including the ones you are reading. If you run two browser agents against
the same browser, one can navigate a tab out from under the other, or reach for
"the active tab" and grab the page you were looking at.

The usual advice is to run each agent on its own port. That means a separate
browser profile, and you lose the logins that made driving your real browser
worth it in the first place.

## What it does

```
agent-browser --cdp 9222           ┐
chrome-devtools-mcp --browser-url  ├──▶  proxy :9222  ──▶  your browser :9229
Playwright connectOverCDP          │     ownership +        real profile,
curl /json → first page            │     focus block        real logins
raw websockets, browser-use …      ┘
```

- `proxy.py` takes port 9222. Your real browser moves to 9229 behind it.
- Every client sees a browser where **the only tabs that exist are the ones it was
  given**. Your tabs and other agents' tabs are filtered out of `Target.getTargets`,
  discovery events and auto-attach.
- Commands that would put an agent in front of you (`Target.activateTarget`,
  `Page.bringToFront`, `/json/activate`) are answered with success and never sent
  to the browser.
- Commands that would hit the whole browser (`Browser.close`, `Browser.crash`,
  `Browser.crashGpuProcess`, `Browser.setWindowBounds`) are swallowed the same
  way. Puppeteer's `browser.close()` ends that client's connection instead of
  quitting your browser.
- Agent tabs are ordinary background tabs in the window you already have open.
  No second window, nothing headless.
- When an agent really does need to show you something, there is
  [the stage](how-it-works.md#the-stage): a queue so only one agent at a time can
  bring a page to the front.

## Who it is for

- People running more than one browser agent (Claude Code sessions, MCP browser
  servers, Playwright or Puppeteer scripts, `browser-use`, `agent-browser`) against
  one Chrome or Brave.
- People who want those agents to use their real logged-in profile without the
  agents touching the tabs they are working in.
- Anyone writing their own agent in Python who wants an exclusive, auto-expiring
  tab lease with a few lines of code (see [Quickstart](quickstart.md)).

## What it is made of

| File | What it is |
|---|---|
| `proxy.py` | The transparent CDP proxy on :9222 plus the REST lease API and stage on :9223. This is what the installer runs. |
| `broker_client.py` | Python client: `lease()`, `show()`, `stage()`, `status()`. |
| `bb-show` | Shell command agents use to ask for the screen. |
| `hooks-example-focus-guard.py` | Example Claude Code `PreToolUse` hook that redirects `open -a <browser>` to `bb-show`. |
| `broker.py` | The first version, a standalone lease broker. Kept for reference; the installer does not run it. |
| `get.sh`, `install.sh`, `launch-browser.sh`, `launchd/` | macOS installer, browser launcher, LaunchAgent. |
| `brave-cdp-guard.sh` | macOS script that reopens a freshly launched Brave with remote debugging on. |
| `windows/` | Windows installer (Task Scheduler), browser launcher and supervisor. |

## Status

An automated suite in `tests/` (157 pytest tests) runs `proxy.py` and the other
components against a fake Chrome DevTools endpoint on every push; see the README's
Status section for what it covers. It does not drive a real browser.
What has been checked by hand is listed in
the README and on the [Install](install.md#platform-support) page: macOS (where it
was developed), Linux for the browser-level CDP path
([issue #1](https://github.com/nicedreamzapp/browser-broker/issues/1)), and
Windows 11 with Brave.

!!! note "Screenshots"
    The repository does not contain any screenshots or GIFs yet.
    TODO-VERIFY: add a screenshot or short recording of two agents working side by
    side and link it here.

browser-broker is MIT licensed. It is built on the Chrome DevTools Protocol and on
the `websockets` and `websocket-client` Python libraries; see [Credits](credits.md).
