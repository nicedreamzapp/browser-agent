---
title: browser-broker FAQ, multiple AI agents in one Chrome
description: >-
  Answers about browser-broker: does it work with Playwright, Puppeteer, MCP and
  Claude Code, can agents see my tabs, does it work on Windows and Linux, why not
  separate profiles, and what it does not do yet.
---

# FAQ

### Do I have to change my agent or tool?

No. The proxy listens on 9222, where your tools already connect, and speaks CDP.
The README lists `agent-browser --cdp 9222`, `chrome-devtools-mcp --browser-url`,
Playwright `connectOverCDP`, Puppeteer, `browser-use`, `curl /json` and raw
websockets. The README reports verification with Playwright's `connect_over_cdp`,
a raw first-page websocket client, two concurrent browser-level clients, and the
REST lease API. TODO-VERIFY: Puppeteer, `browser-use`, `agent-browser` and
`chrome-devtools-mcp` are named as supported, but no recorded test for each was found in the repo.

### Can an agent see or use the tabs I have open?

No. The proxy only hands out tabs it created. Your tabs are filtered out of every
target list and event stream, and commands naming them return
`No target with given id found`. The proxy's `/adopt` returns `410`.

### Can two agents end up in the same tab?

Not through the proxy. Each browser-level connection is its own owner. A tab
listed by `GET /json` is reserved for that caller for 10 seconds and accepts one
page socket. A REST lease belongs to the lease holder.

### Will an agent steal focus or bring a tab to the front?

Not through CDP: `Target.activateTarget`, `Page.bringToFront` and `/json/activate`
are swallowed. macOS commands such as `open -a` go around the proxy, which is why
`bb-show` and the example Claude Code hook exist.

### Why not just give each agent its own browser profile?

A separate profile doesn't have your logins. browser-broker exists so agents can use
the real, logged-in profile without stepping on each other or on you.

### Does it open a new window or run headless?

Neither. Agent tabs are ordinary background tabs in the window you already have open.
An earlier version parked agent tabs in an off-screen window; that was dropped on
2026-09-07 because the extra window pushed the human's window aside
(see the [changelog](changelog.md)).

### Does it work on Windows and Linux?

Yes, with limits on what has been checked. Windows 11 with Brave was verified with
the installer in `windows/`. Linux was verified for the browser-level CDP path on
Ubuntu 26.04 / Chrome 153 by an outside tester in
[issue #1](https://github.com/nicedreamzapp/browser-broker/issues/1). See
[platform support](install.md#platform-support).

### Does it work with Chrome or only Brave?

Both. Brave is the default in the launch scripts; set `BROWSER_APP="Google Chrome"`
on macOS or `BROWSER_EXE` on Windows. The proxy itself doesn't care which
Chromium browser is upstream. The macOS and Windows "reopen with debugging on"
guards only look for Brave.

### Why do pages with animations look frozen in agent tabs?

`requestAnimationFrame` doesn't fire in hidden documents. See
[Troubleshooting](troubleshooting.md#pages-look-blank-in-an-agent-tab).

### What happens if an agent crashes?

Its tabs are cleaned up. A browser-level client's tabs close when its socket drops.
A legacy client's tab closes when the page socket closes. A REST lease expires after
its TTL (300 s by default) without a renew, and the reaper closes the tab within 5 seconds.

### What happens if the proxy crashes?

The LaunchAgent (macOS) or `run-broker.ps1` (Windows) restarts it. On startup it
reads `tabs.json` and closes the tabs it had opened before, so they aren't left
in your window.

### Is it secure?

It is localhost only, with no authentication. Anyone who can reach 127.0.0.1:9223
or 9222 can drive your logged-in browser, which is already true of 9229 once
remote debugging is on. The isolation is between cooperating tools, not a defence
against hostile local software.

### Is this the same as an MCP server?

No. It sits below MCP servers, Playwright and the rest, and hands them plain CDP.
Any of those can run on top of it.

### How big is it?

`proxy.py` is 961 lines of Python with one third-party dependency (`websockets`).
`broker_client.py` needs `websocket-client`. (Line count from the repo at the time
this page was written.)

### What is not built yet?

From the README: a lane router that skips the browser when a job is really an API
call, and per-domain serialization so two agents never hit one site as the same
user at once. From issue #1: a lease option that keeps a tab actually rendering.
There is no automated test suite yet.

### What is `broker.py`?

The first version: a standalone lease broker that talks to the browser directly
and can `/adopt` an existing tab when asked by id. It is kept for reference; the
installer runs `proxy.py`.
