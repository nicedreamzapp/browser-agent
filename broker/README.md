<div align="center">

# 🚦 browser-broker

### Your second agent just stole the tab your first one was using.

**One logged-in browser · many agents · nobody fights, and nobody touches your tabs**
**one Python proxy · it *is* port 9222, so no tool changes a single line**

[![Tests](https://img.shields.io/github/actions/workflow/status/nicedreamzapp/browser-agent/broker-tests.yml?branch=main&style=for-the-badge&label=broker%20tests)](https://github.com/nicedreamzapp/browser-agent/actions/workflows/broker-tests.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-6366f1.svg?style=for-the-badge)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-22c55e.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Zero tool changes](https://img.shields.io/badge/tool_changes-zero-blue?style=for-the-badge)](#it-is-port-9222)
[![Self-hosted](https://img.shields.io/badge/local-only-22c55e?style=for-the-badge)](#security)

</div>

<a id="why"></a>

> browser-broker now lives here, in the `broker/` folder of [browser-agent](https://github.com/nicedreamzapp/browser-agent). The agent does the work; the broker makes it safe to share your real, logged-in browser with it. Start at the [main README](../README.md); this page is the broker's full reference.
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s01-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s01-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s01-banner.svg" width="100%" alt="01. Why it exists. Two agents, one browser"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s01-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s01-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s01-r1.svg" width="100%" alt="One tab each. Each agent gets its own private tab in your real, logged-in browser. Zero tool changes. Your tools keep dialing port 9222 exactly as they do today. Tab ownership. Agents cannot see or steal each other&#x27;s tabs, or yours."></picture>

**browser-broker is a local proxy that sits on Chrome's debug port (9222) and gives each AI agent its own private tab in your real, logged-in browser, so agents cannot see or steal each other's tabs or yours.**

Proof in this repo: the Linux isolation check in [issue #1](https://github.com/nicedreamzapp/browser-broker/issues/1) (two clients, each blind to the other's tabs, checked in both directions), the stage timing run in [🎦 The stage](#the-stage), the manual test runs listed under [📊 Status](#status), and the automated suite in [`tests/`](tests/), which CI runs on every push.

If you just ran a second browser agent and watched it **navigate a tab out from under the first one** — or reach for "the active tab" and grab the page *you* were reading — that is what this fixes.

And if you have ever stepped in to help an agent, typed into the form or solved its captcha, and watched it reload the page and wipe your work: the broker now notices you and keeps every agent's hands off that tab. See [🤝 When the human helps](#when-the-human-helps).

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/get.sh | bash
```

Nothing else changes. Your tools keep dialing port 9222 exactly as they do today.

The Chrome DevTools Protocol has **no concept of ownership**. Every agent sees every tab, including yours, and none of them can tell that another one is already working in the tab it just grabbed. The usual advice is "run them on different ports," which means a different browser, which means losing the logins that made your real browser worth driving in the first place.

browser-broker adds the missing layer: a process that owns the debug port and hands each agent an **exclusive lease** on its own tab.

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s01-rule.svg" width="100%" alt="">


<a id="it-is-port-9222"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s02-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s02-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s02-banner.svg" width="100%" alt="02. It is port 9222. A transparent proxy in front of your browser"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s02-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s02-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s02-r1.svg" width="100%" alt="Port 9222. The proxy takes it; your real browser moves to 9229 behind it. Focus blocked. Calls that would steal your screen are answered and never forwarded. Tabs you did not open do not exist. They are invisible to every agent."></picture>

`proxy.py` takes 9222. Your real browser moves to 9229 behind it. Every tool keeps its existing command line and gets a browser in which **the only tabs that exist are the ones it was given**:

```
agent-browser --cdp 9222           ┐
chrome-devtools-mcp --browser-url  ├──▶  proxy :9222  ──▶  your browser :9229
Playwright connectOverCDP          │     ownership +        real profile,
curl /json → first page            │     focus block        real logins
raw websockets, browser-use …      ┘
```

- `Target.getTargets`, `setDiscoverTargets`, `attachToTarget` and auto-attach events are all filtered to the caller's own tabs. Your tabs and other agents' tabs are not merely off-limits — **they do not exist** from where that client stands.
- `Target.activateTarget`, `Page.bringToFront` and `/json/activate` are answered with success and **never forwarded**. Nothing an agent does can put itself in front of you.
- `Browser.close`, `Browser.crash` and `Browser.setWindowBounds` are swallowed the same way. Puppeteer's `browser.close()` sends `Browser.close`, and through a plain CDP port that quits your whole browser; here it just ends that client's connection and closes its tabs.
- `PUT /json/new` (what Chrome 111+ clients send) works: plain HTTP on :9222 is answered by the proxy itself, and websocket upgrades are piped to it byte for byte.
- A tab from `/json` is reserved for the caller that listed it, and only one page socket may hold it, so two legacy clients never end up driving the same tab.
- The proxy writes down the tabs it opened (`tabs.json`). If it crashes or restarts, it closes those leftovers on the way back up instead of leaving them in your window. Tabs your browser restores after *it* restarts come back with new ids and can't be told apart from yours, so those are left alone.
- A legacy client that does `GET /json` and grabs the first page gets its own fresh tab, because that is the only page in its list.
- A browser-level websocket is one client for as long as it stays connected; its tabs go away when it does.

Verified with Playwright's `connect_over_cdp`, a raw first-page websocket client, two concurrent browser-level clients, and the REST lease API — Gmail, LinkedIn and Yahoo Mail all loaded logged in, with the human's tabs untouched throughout.

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s02-rule.svg" width="100%" alt="">


<a id="direct"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s03-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s03-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s03-banner.svg" width="100%" alt="03. Using it directly. The lease client and why it works"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s03-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s03-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s03-r1.svg" width="100%" alt="Auto-renew. The lease renews while you work and releases on exit. Dumb rule. Agents only get tabs the broker opened. Leases expire. 300 s by default, so no deadlocks."></picture>

```python
from broker_client import lease

with lease("my-agent", url="https://mail.google.com", purpose="inbox sweep") as tab:
    tab.eval("document.title")   # you, alone, in a tab nobody else can touch
```

The lease auto-renews while you work and releases on exit, including on an exception. A crashed agent frees its tab instead of stranding it.

**Agent tabs are ordinary background tabs.** In your window, behind what you are looking at. No second window, nothing parked off-screen, nothing headless — headless can't use the profile that has your logins, and a stray window just shoves yours aside. Isolation was never the window's job: the ownership registry and the swallowed focus commands do all of it.

**The rule is deliberately dumb.** Agents only ever get tabs the broker opened. Anything *you* opened is invisible to every agent, permanently, with no heuristics. Focus detection was rejected on purpose — window checks false-negative across macOS Spaces, and a heuristic that is wrong once is worse than a rule that is boring. The proxy has no way around this: its `/adopt` answers `410`. Only the older standalone [`broker.py`](broker.py) can adopt an existing tab, and only when asked by name.

**Leases expire.** 300 s by default, auto-renewed every 60 s by the client, reaped every 5 s. No deadlocks.

**It reconnects.** Browsers auto-update, crash, get quit. If the proxy's control socket to the browser drops, the next call opens a fresh one, and the LaunchAgent restarts the proxy if it exits.

**It has a heartbeat.** One log line an hour. A silent daemon is indistinguishable from a dead one.

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s03-rule.svg" width="100%" alt="">


<a id="the-stage"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s04-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s04-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s04-banner.svg" width="100%" alt="04. The stage. When an agent does want the screen"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s04-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s04-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s04-r1.svg" width="100%" alt="bb-show. One agent holds the screen at a time; the next one waits its turn. Cooperative. Works because the polite path is one word shorter than the rude one."></picture>

Everything above is about keeping agents out of your way. Sometimes one genuinely has something
to show you, and that is the opposite problem.

There is no CDP call to intercept for this. `open -a "Brave Browser" <url>` and AppleScript
`activate` are macOS calls, so swallowing `Target.activateTarget` does nothing about them. Two
Claude Code sessions ran `open -a` four minutes apart on this machine and threw the window back
and forth between them while the proxy sat there with a clean log.

So the stage is a queue in front of the one door, and it is easier to use than the rude way:

```bash
bb-show https://example.com my-agent "why you are interrupting"
```

One agent holds the screen at a time. A second one waits its turn instead of yanking the window
mid-sentence, and if the wait runs out it is told who has it and for how long, so it can print
the link and move on rather than fight. Showing a page that is already open brings that tab
forward as it is, never a second copy and never a reload, so there is never a row of
half-current duplicates of the same page.

<a id="when-the-human-helps"></a>
### 🤝 When the human helps: five rules

**The problem.** On the night of October 22, 2026, agents fought the human over the same browser
tab. An agent opened an opt-out form, started filling it in, closed the tab, opened it again and
started over, more than ten times, right in front of him. When he stepped in to help, typing in
the form or solving the captcha himself, the agent never noticed. It reloaded or re-showed the
page and wiped his work, or opened the same link on top of the page he had just submitted. And it
left the tabs open afterwards.

We found four causes. The agent hopped between a hidden copy and a visible copy of the same
page. Its fix for anything odd was to show the page again, and showing an already-open page
reloaded it in place. Several agents acted in the browser at once, and one restarted the broker
mid-task. And no agent ever read what the page showed right now, which is the only way to know
what the human had already done.

**The fix is five rules, and the broker enforces each one in code.** An agent does not have to
remember them, and cannot talk its way around them.

| Rule | What the broker does |
|---|---|
| **1. One tab per job.** | The owner name an agent passes is its job. A job owns one tab, pinned to one site, until `bb-close`. Showing the same site again returns that same tab. Leasing a hidden tab on a site the job already shows returns the shown tab, and showing a site the agent already has a hidden tab on brings that tab forward. There is never a second copy to hop to. |
| **2. Look before every step.** | `bb-show` of an open page brings it forward as it is and prints what it shows: URL, title, text, filled-in fields, and whether the human is working in it. `bb-peek` reads the same state without showing anything (passwords and card numbers masked), for any tab, including the human's own. |
| **3. Notice when the human helps.** | Every tab the broker opens or shows gets a small listener for real keyboard and mouse input (`event.isTrusted`, which page scripts cannot fake). Input the agent itself sends through the broker is recognized and not counted. While the human has been active in the last 20 seconds, agent clicks and keystrokes in that tab are refused, and a refused `bb-show` exits 4 and hands back what he has done so far, so the agent continues from there. |
| **4. No reloading or closing mid-job.** | A page that is already open is never reloaded unless the caller passes `--reload`, and never while the human is working in it. A tab on screen for an open job cannot be reloaded, re-navigated to the same page or closed through the proxy. A tab the human is working in is never closed by anything: not `bb-close`, not a released or expired lease, not a disconnecting client, not a restarted broker. It is handed to him instead. |
| **5. Stuck means ask, not retry.** | If the same job opens and closes (or reloads) the same page twice within 10 minutes, the third try is refused with HTTP 429 and `bb-show` exits 5 with one instruction: stop and ask the human in one line what is wrong. |

What an agent sees:

```text
$ bb-show https://people.example/optout tps
shown: https://people.example/optout
already open: brought forward AS IS, NOT reloaded (no duplicate opened)
  now at: https://people.example/optout/success
  title:  Request received
  shows:  Thank you. Your opt-out request has been submitted.
  filled: Email = matt@example.com
  the human worked in it (last input 41s ago)
LOOK at that before acting: the human may already have filled, submitted or finished this page.

$ bb-show --reload https://people.example/optout tps
the human is working in that tab right now. Not reloaded, not reopened.     (exit 4)

$ bb-show https://people.example/optout tps
stop: "tps" has opened and closed https://people.example/optout 2 times in the last 10 minutes.
It was not opened again. Do not retry. Ask the human in one line what is wrong.   (exit 5)
```

From Python, `broker_client.peek(owner)` returns the same state, `broker_client.human_active(owner)`
answers yes or no, and `show()` raises `HumanIsWorking` (with `.page`) or `StopAndAsk`.
`BROKER_HUMAN_QUIET` (20 s), `BROKER_CHURN_WINDOW` (600 s) and `BROKER_CHURN_LIMIT` (2) tune it.
Every rule has its own tests in [`tests/test_five_rules.py`](tests/test_five_rules.py).

One honest limit: a captcha inside a cross-site frame keeps its clicks to itself, so the broker
counts the human's focus moving into that frame as activity rather than each click inside it.

### 🧹 Close what you open

**The problem.** Agents kept leaving tabs open after they finished their jobs. Every invoice,
report and lookup they put up stayed there, and the tabs piled up in Matt's browser across all
three machines (the M5, the mini and the Windows PC) until he had to keep asking for them to be
cleared. A page shown to the human with `bb-show` stayed open forever, because nothing in the
tool ever closed it.

**What the tool does now.** `bb-show` reminds the caller to close the page every time it shows
one, and `bb-close` closes it. Hidden work tabs are closed outright when their lease is released,
expires or its client disconnects. The rule for any agent using browser-broker:
**close every tab you opened when the task is done.**

A page you put up stays on the human's screen until
something closes it, so close it yourself the moment the task is done, before you report back:

```bash
bb-close my-agent              # by the owner name you gave bb-show
bb-close example.com/report    # or by any part of the page URL
```

`bb-show` prints this reminder every time it shows a page. `bb-close` only closes tabs the broker
itself opened; a tab the human already had open, or is working in right now, is left to him.
Hidden work tabs close on their own: a lease's tab is closed (not just released) when the lease
is released, when its TTL runs out, or when a browser-level client disconnects, and
`broker_client.lease()` releases on exit, exceptions included.

**Tabs the site opens count too (2026-10-29).** Submitting a form can make the site open a new
tab by itself (`target=_blank`, `window.open`, an OAuth popup). The broker never opened that tab,
so it used to stay behind after the agent reported its tabs closed. Chrome names the tab that
opened every page (`openerId`), so a page opened from a broker tab is now that owner's child tab,
grandchildren included. It closes with its parent on release, expiry, disconnect and `bb-close`,
under the same rules: one the human is typing or clicking in, worked in, or opened with his own
click is left open and reported (`children_left_open` from `/release`, `left_open` from
`bb-close`). A tab opened from one of the human's own tabs is never recorded. `/status`, `/stage`
and `bb-peek` list child tabs under their owner.

```
A: HTTP 200 after 0.7s   shown
B: HTTP 409 after 0.0s   stage held by agent-A for another 7.0s
C: HTTP 200 after 7.9s   waited, then shown
```

| | |
|---|---|
| `POST /show` | `{owner, url, purpose, wait, dwell, reload}` → `{shown, target_id, held_for, already_open, moved, reloaded, page}`; `409` screen busy, `423` the human is working in that tab (with `page`), `429` stop and ask |
| `POST /unshow` | `{match}` (owner or URL part) → `{closed, left_open}`, what `bb-close` calls |
| `GET /peek?match=` | read-only state of matching tabs: `url, title, text, fields, human`, what `bb-peek` calls |
| `POST /unstage` | drop the hold early |
| `GET /stage` | who has the screen and what is on it |

`BROKER_STAGE_DWELL` (20s, how long a shown page is protected) and `BROKER_STAGE_WAIT` (30s, how
long a caller queues) tune it. This is cooperative: it works because the polite path is one word
shorter than the rude one. On a machine running Claude Code you can make it binding with a
`PreToolUse` hook that denies `open -a <browser>` and points at `bb-show` instead;
[`hooks-example-focus-guard.py`](hooks-example-focus-guard.py) is one (its shebang and log path are
from the author's machine, so adjust them). If the broker is down, `bb-show` falls back to a plain
`open -a`, which is macOS only.

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s04-rule.svg" width="100%" alt="">


<a id="install"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s05-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s05-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s05-banner.svg" width="100%" alt="05. API and install. One-line install, REST lease API"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s05-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s05-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s05-r1.svg" width="100%" alt="One line. curl get.sh | bash installs it on macOS. REST on :9223. Lease, renew, release, status and health endpoints. Python 3.9+. macOS, Windows and Linux notes below."></picture>

| | |
|---|---|
| `POST /lease` | `{owner, url, purpose, ttl}` → `{lease_id, target_id, ws_url, expires_in, shared_with_shown_job}` |
| `POST /renew` | `{lease_id, ttl}` |
| `POST /release` | `{lease_id}` → `{released, tab_closed, left_open}`. Closes the tab, unless it is a job tab on screen (`bb-close` finishes that) or the human is working in it |
| `POST /adopt` | returns `410` on the proxy. Only the older [`broker.py`](broker.py) supports it |
| `GET /status` | who holds what, for how long, and whether the browser is alive |
| `GET /health` | |

`ws_url` is a normal CDP page socket on the proxy's port (`ws://127.0.0.1:9222/devtools/page/<id>`). Drive it with the bundled client, Playwright's `connect_over_cdp`, or raw websockets. The traffic goes through the proxy, which is how it enforces who is allowed in.

Everything is configured by environment: `BROKER_PROXY_PORT` (9222, what tools dial), `BROKER_UPSTREAM_PORT` (9229, your real browser), `BROKER_PORT` (9223, lease API), `BROKER_TTL`, `BROKER_HEARTBEAT`, `BROKER_LOG`, and `BROKER_DEBUG=1` for a handshake trace.

### 🚀 Install

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/get.sh | bash
```

Uninstall (keeps your browser and profile; add `-s -- --purge` to delete the clone too):

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-agent/main/broker/uninstall.sh | bash
```

Or by hand:

```bash
git clone https://github.com/nicedreamzapp/browser-agent && cd browser-agent/broker
pip3 install websockets websocket-client
./launch-browser.sh   # your real profile, CDP on :9229
./install.sh          # LaunchAgent for proxy.py: :9222 for tools, :9223 lease API
curl localhost:9223/status
```

Already have something on 9222 you can't restart? Try it on another port first:
`BROKER_PROXY_PORT=9224 BROKER_UPSTREAM_PORT=9222 BROKER_PORT=9225 python3 proxy.py`

Python 3.9+. `proxy.py` needs [`websockets`](https://github.com/python-websockets/websockets) (13 or newer, for `websockets.asyncio`); `broker_client.py` and `broker.py` need `websocket-client`. `install.sh` picks one Python (`/usr/bin/python3` first, or `BROKER_PYTHON`), makes sure it has both, and writes its full path into the LaunchAgent, because launchd does not see your shell's PATH. Brave is the default; for Chrome: `BROWSER_APP="Google Chrome" ./launch-browser.sh`.

**Windows / Linux:** `proxy.py`, `broker.py` and `broker_client.py` are pure Python and don't care about the OS — only the two shell scripts are macOS (Windows has its own in `windows/`, below). Launch your browser with `--remote-debugging-port=9229 --remote-allow-origins=* --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling` (the same flags `launch-browser.sh` uses), then run `python proxy.py` from a startup task. `bb-show`'s fallback and the hook example are macOS only. Those backgrounding flags matter: agent tabs are background tabs, and without the flags Chrome throttles a background tab's timers to roughly once a second, which is enough to mount zero rows in a modern SPA. **What the flags do not do is make a hidden tab paint.** `requestAnimationFrame` does not fire in a document whose `visibilityState` is `hidden` — that is Page Visibility behaviour, not throttling, and no flag turns it back on. So a page that renders off timers or fetch callbacks fills in normally in an agent tab, and a page that renders inside a rAF loop (canvas, animation-driven charts) will sit at its initial frame. `Page.captureScreenshot` still returns a real PNG either way, so a screenshot is not proof the page is live. Developed on macOS. **Linux is verified for the browser-level CDP path** ([#1](https://github.com/nicedreamzapp/browser-broker/issues/1)): Ubuntu 26.04 / Chrome 153, two concurrent browser-level clients each saw only its own tabs, and cross-client `Target.attachToTarget` returned `No target with given id found` in both directions. Hidden-tab rendering was measured there too and reproduces on macOS: after 4s a hidden lease tab had fired 0 animation frames while its 100ms timer kept ticking, on both Ubuntu/Chrome 153 and macOS/Brave. Not yet covered there: a logged-in profile, Playwright `connectOverCDP`, the legacy `/json` first-page path, and multi-hour runs. **Windows is verified** (2026-10-02, Windows 11, Brave on Chromium 154, Python 3.14, logged-in real profile): `powershell -NoProfile -ExecutionPolicy Bypass -File windows\install.ps1` installs the two Python deps, registers a Task Scheduler task `browser-broker` (at logon, your user, interactive session, no time limit) and starts it. The task runs `windows\run-broker.ps1`, which runs `windows\launch-browser.ps1` once (exits if 9229 already answers, otherwise closes a CDP-less browser gracefully and relaunches it with the flags) and then keeps `proxy.py` alive under `pythonw` (no console window), restarting it 10s after any exit. Verified on that box: `/health` returned `{"ok": true, "brave": true}`, `/json/version` answered on 9222, a REST lease of a hidden tab evaluated `document.title` and closed on release, a legacy `/json` client and a raw browser-level client each saw only their own tab and never the human's, the stage opened a page in the human's window, and killing `proxy.py` brought it back on 9222/9223 within 10s. On Windows the stage relies on `Target.activateTarget` alone (the macOS app-activation step is skipped). Not covered yet: the close-and-relaunch path when the browser is already running without CDP, and a cold reboot. Logs: `launcher.log` and `broker.log` in the repo directory, `proxy.err` for Python tracebacks. If the browser is closed and reopened from its normal shortcut it comes back without CDP; run `windows\launch-browser.ps1` again (or sign out and back in).

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s05-rule.svg" width="100%" alt="">


<a id="status"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s06-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s06-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s06-banner.svg" width="100%" alt="06. Security and status. What is tested, what is not"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s06-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s06-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s06-r1.svg" width="100%" alt="Localhost only. No auth. Do not expose 9222 or 9223. 167 pytest tests. Run on every push in CI, no browser or network needed."></picture>

<a name="security"></a>
### <a name="security"></a>🔒 Security

Localhost only, no auth. Anything that can reach `127.0.0.1:9223` can drive your logged-in browser — which is already true of `127.0.0.1:9222` the moment you enable remote debugging. Don't expose either.

<a name="status"></a>
### <a name="status"></a>📊 Status

Built in one night and proven with four manual tests: a leased tab loading Gmail authenticated while the human's visible tab sat on a different account; two agents running concurrently on separate targets; a simulated agent crash whose lease expired and was reclaimed; the human's tabs untouched throughout. Those manual runs are not checked in.

What is checked in is an automated suite in [`tests/`](tests/): 248 pytest tests that run the real `proxy.py`, `broker.py`, `broker_client.py`, `bb-show` and the hook example against a small fake Chrome DevTools endpoint ([`tests/fake_browser.py`](tests/fake_browser.py)) on loopback ports. It covers the ownership rules end to end (two clients blind to each other and to the human's tabs, focus stealers and `Browser.close` swallowed, tabs released on disconnect), the `/json` legacy path, the REST lease API, the stage queue, reaping and `tabs.json`. It needs no browser and no network, and runs on Linux in about 20 seconds. Two checks are macOS only (real `osascript`, `plutil`) and are skipped elsewhere. It does not drive a real Chrome, so the manual runs above still matter.

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Not yet built: a lane router that skips the browser entirely when a job is really an API call, and per-domain serialization so two agents never hammer one site as the same user.

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s06-rule.svg" width="100%" alt="">


<a id="around"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s07-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s07-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s07-banner.svg" width="100%" alt="07. Around the project. The stack, prior art and license"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s07-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s07-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s07-r1.svg" width="100%" alt="Local-first stack. One piece of a set that runs entirely on your own machine. Prior art. Several neighbours attack the same problem from other directions."></picture>

### 🧩 The local-first stack

One piece of a set that runs entirely on your own machine, no cloud:

| Project | Role | What it does |
|---|---|---|
| 🧠 [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) | Brain | Claude Code on a local MLX model — the inference the agents think with |
| 🌐 [browser-agent](../README.md) | Hands | Drives your real browser via CDP — iframes, Shadow DOM, ProseMirror |
| 🚦 **browser-broker** (this folder) | Traffic cop | Leases each agent its own tab so hands never collide, or grab yours |

### 🤝 Prior art and neighbours

This problem is being attacked from several directions right now. These aren't competitors so much as pieces of the same puzzle, and browser-broker borrows from the ideas in them:

- [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) — `--cdp` mode with `--pin-tab` binds a session to one tab; [issue #1068](https://github.com/vercel-labs/agent-browser/issues/1068) asks for `Target.createBrowserContext` so parallel sessions get isolated cookies.
- [leeguooooo/chrome-use](https://github.com/leeguooooo/chrome-use) — per-session coloured tab groups in your real Chrome, never force-fronts a tab.
- [mediar-ai/playwright-mcp-orchestrator](https://github.com/mediar-ai/playwright-mcp-orchestrator) — one Chrome, many `@playwright/mcp` sessions, each scoped to its own tabs.
- [henu-wang/chrome-mcp-proxy](https://github.com/henu-wang/chrome-mcp-proxy) and `chrome-devtools-mcp-mux` — proxies in front of `chrome-devtools-mcp` that block focus-stealing and give each client its own tabs.
- [captivus/chrome-agent](https://github.com/captivus/chrome-agent), [pasky/chrome-cdp-skill](https://github.com/pasky/chrome-cdp-skill), [ofershap/real-browser-mcp](https://github.com/ofershap/real-browser-mcp) — drive your real, logged-in browser over CDP or an extension.

What browser-broker adds is a layer that sits *below* all of them. It doesn't care which tool the agent is — a CLI, an MCP server, Playwright, raw websockets — because it hands out plain CDP page sockets. The ownership rule and the transparent 9222 endpoint are the parts that don't exist elsewhere yet. If you maintain one of the projects above and want a lease layer under yours, open an issue.

### 📜 License

MIT — see [LICENSE](LICENSE). Built on other people's work — see [CREDITS.md](CREDITS.md).

<details>
<summary><b>🛠️ What I built</b> (file by file)</summary>

Designed and written by Matt Macosko:

- [`proxy.py`](proxy.py): the transparent CDP proxy on :9222. Filters `Target.*` calls and events down to the caller's own tabs, swallows focus-stealing commands, ties tab ownership to the client connection, and serves the REST lease API and the stage on :9223. This is what the installer runs.
- [`broker_client.py`](broker_client.py): the Python client. `lease()` context manager with auto-renew and release-on-exit, plus `show()`, `unshow()` and `stage()`.
- [`bb-show`](bb-show): the shell command agents use to ask for the screen instead of `open -a`.
- [`bb-close`](bb-close): closes what `bb-show` put up once the task is done (by owner or URL substring). Rule: close every tab you opened.
- [`hooks-example-focus-guard.py`](hooks-example-focus-guard.py): an example Claude Code `PreToolUse` hook that redirects `open -a <browser>` and AppleScript `activate` to `bb-show`.
- [`broker.py`](broker.py): the first version, a standalone lease broker with `/adopt`. Kept for reference; the installer does not run it.
- [`get.sh`](get.sh), [`install.sh`](install.sh), [`uninstall.sh`](uninstall.sh), [`launch-browser.sh`](launch-browser.sh), [`launchd/com.browser-broker.plist`](launchd/com.browser-broker.plist): the macOS one-line installer, browser launcher and LaunchAgent.
- [`windows/install.ps1`](windows/install.ps1), [`windows/launch-browser.ps1`](windows/launch-browser.ps1), [`windows/run-broker.ps1`](windows/run-broker.ps1): the Windows installer (Task Scheduler at-logon task), browser launcher and the supervisor that keeps `proxy.py` alive.

Upstream, not mine: the [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) and the Chrome/Brave browsers (Chromium team), and the [`websockets`](https://github.com/python-websockets/websockets) and [`websocket-client`](https://github.com/websocket-client/websocket-client) Python libraries. See [CREDITS.md](CREDITS.md).

</details>

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-broker/03e3d97ce3225fc44df9b6a14a50b40adeaffa66/assets/readme/s07-rule.svg" width="100%" alt="">

