---
title: browser-broker changelog
description: >-
  What changed in browser-broker, by date, taken from the git history: the
  transparent CDP proxy, background tabs instead of off-screen windows, the stage,
  Windows support, and proxy hardening.
---

# Changelog

The project has no tagged releases yet, so this list is grouped by commit date
from the git history on `main`. Commit hashes link to GitHub.

## 2026-10-06

- **Proxy hardening** ([84248ea](https://github.com/nicedreamzapp/browser-broker/commit/84248ea)).
  `Browser.close`, `Browser.crash`, `Browser.crashGpuProcess` and
  `Browser.setWindowBounds` are swallowed, so Puppeteer's `browser.close()` no
  longer quits your browser. `Target.attachToBrowserTarget` is refused. A new front
  door on 9222 answers plain HTTP itself, so `PUT /json/new` works. `/json` reserves
  the tab it lists, a pool tab takes one page socket, and `/json/close` only closes
  pool tabs. Owned tabs are recorded in `tabs.json` and closed on the next start.
  `browserContextId` is honoured (Playwright `new_context` no longer hangs) and a
  client's contexts are disposed when it leaves. An error in one command no longer
  ends the session. The stage releases its lock on failure. `broker.log` rotates at
  5 MB and launchd's stdio files are trimmed past 10 MB. `install.sh` pins one Python
  with `websockets>=13`, and the client keepalive retries instead of giving up.
- **brave-cdp-guard.sh** ([94249b0](https://github.com/nicedreamzapp/browser-broker/commit/94249b0)):
  on macOS, reopens a Brave under 90 seconds old that came up without remote debugging.
- **Windows supervisor** does the same for `brave.exe`
  ([2bb62b6](https://github.com/nicedreamzapp/browser-broker/commit/2bb62b6)).

## 2026-10-02

- **Windows support** ([064cea0](https://github.com/nicedreamzapp/browser-broker/commit/064cea0)):
  `windows/install.ps1` (Task Scheduler at-logon task), `launch-browser.ps1` and
  `run-broker.ps1`. Verified on Windows 11, Brave (Chromium 154), Python 3.14.
- **Stage: one tab per agent** ([b06d599](https://github.com/nicedreamzapp/browser-broker/commit/b06d599)).
  The stage used one shared tab, so each agent's `show` closed the page another
  agent had just shown. Each owner now has its own stage tab.

## 2026-09-27

- README rewrite: plain summary, "What I built", and fixes where the text disagreed
  with `proxy.py` ([b8be446](https://github.com/nicedreamzapp/browser-broker/commit/b8be446)).

## 2026-09-20

- **The stage** ([01d16b9](https://github.com/nicedreamzapp/browser-broker/commit/01d16b9)):
  `POST /show`, `POST /unstage`, `GET /stage`, the `bb-show` command and the example
  Claude Code hook. Verified with three concurrent callers: A shown at 0.7 s, B
  refused with `409` at 0.0 s, C queued and shown at 7.9 s.
- **Hidden tabs don't paint** ([5fb3028](https://github.com/nicedreamzapp/browser-broker/commit/5fb3028)).
  Corrected the docs: the backgrounding flags keep timers running but
  `requestAnimationFrame` never fires in a hidden tab. Found by
  [@Hronom](https://github.com/Hronom) in [issue #1](https://github.com/nicedreamzapp/browser-broker/issues/1).
- Credited Hronom in CREDITS.md ([6b1d340](https://github.com/nicedreamzapp/browser-broker/commit/6b1d340)).

## 2026-09-19

- README: Linux verified for the browser-level CDP path, with what the test did
  not cover ([84d10b2](https://github.com/nicedreamzapp/browser-broker/commit/84d10b2)).

## 2026-09-08

- `broker.py` also switched to background tabs; the off-screen window code was
  removed everywhere. README now opens on the tab collision. Added the one-line
  installer `get.sh` ([851962e](https://github.com/nicedreamzapp/browser-broker/commit/851962e)).

## 2026-09-07

- **Agent tabs are ordinary background tabs** ([36d57cc](https://github.com/nicedreamzapp/browser-broker/commit/36d57cc)).
  The off-screen window at x=-4200 was dropped from `proxy.py` because the extra
  window pushed the human's window aside. Isolation never depended on the window.

## 2026-09-05

- **First commit** ([821fe6d](https://github.com/nicedreamzapp/browser-broker/commit/821fe6d)):
  `broker.py` lease broker, `broker_client.py`, macOS launcher and LaunchAgent.
- **proxy.py** ([26d4c6a](https://github.com/nicedreamzapp/browser-broker/commit/26d4c6a)):
  the transparent CDP endpoint on 9222, with the browser moved to 9229. Verified
  with Playwright (three pages including Gmail and LinkedIn logged in), a raw
  first-page client on Yahoo, two concurrent clients, and REST leases.
- Each hidden tab briefly got its own off-screen window
  ([ec7216a](https://github.com/nicedreamzapp/browser-broker/commit/ec7216a));
  reverted two days later.
- README additions: Windows/Linux note, the local-first stack, prior art.
