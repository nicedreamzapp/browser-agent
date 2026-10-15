<div align="center">

# 🚦 browser-broker

### Your second agent just stole the tab your first one was using.

**One logged-in browser · many agents · nobody fights, and nobody touches your tabs**
**one Python proxy · it *is* port 9222, so no tool changes a single line**

[![Tests](https://img.shields.io/github/actions/workflow/status/nicedreamzapp/browser-broker/ci.yml?branch=main&style=for-the-badge&label=tests)](https://github.com/nicedreamzapp/browser-broker/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-6366f1.svg?style=for-the-badge)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-22c55e.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Zero tool changes](https://img.shields.io/badge/tool_changes-zero-blue?style=for-the-badge)](#it-is-port-9222)
[![Self-hosted](https://img.shields.io/badge/local-only-22c55e?style=for-the-badge)](#security)

</div>

**browser-broker is a local proxy that sits on Chrome's debug port (9222) and gives each AI agent its own private tab in your real, logged-in browser, so agents cannot see or steal each other's tabs or yours.**

Proof in this repo: the Linux isolation check in [issue #1](https://github.com/nicedreamzapp/browser-broker/issues/1) (two clients, each blind to the other's tabs, checked in both directions), the stage timing run in [🎦 The stage](#the-stage), the manual test runs listed under [📊 Status](#status), and the automated suite in [`tests/`](tests/), which CI runs on every push.

---

## 🛠️ What I built

Designed and written by Matt Macosko:

- [`proxy.py`](proxy.py): the transparent CDP proxy on :9222. Filters `Target.*` calls and events down to the caller's own tabs, swallows focus-stealing commands, ties tab ownership to the client connection, and serves the REST lease API and the stage on :9223. This is what the installer runs.
- [`broker_client.py`](broker_client.py): the Python client. `lease()` context manager with auto-renew and release-on-exit, plus `show()` and `stage()`.
- [`bb-show`](bb-show): the shell command agents use to ask for the screen instead of `open -a`.
- [`hooks-example-focus-guard.py`](hooks-example-focus-guard.py): an example Claude Code `PreToolUse` hook that redirects `open -a <browser>` and AppleScript `activate` to `bb-show`.
- [`broker.py`](broker.py): the first version, a standalone lease broker with `/adopt`. Kept for reference; the installer does not run it.
- [`get.sh`](get.sh), [`install.sh`](install.sh), [`launch-browser.sh`](launch-browser.sh), [`launchd/com.browser-broker.plist`](launchd/com.browser-broker.plist): the macOS one-line installer, browser launcher and LaunchAgent.
- [`windows/install.ps1`](windows/install.ps1), [`windows/launch-browser.ps1`](windows/launch-browser.ps1), [`windows/run-broker.ps1`](windows/run-broker.ps1): the Windows installer (Task Scheduler at-logon task), browser launcher and the supervisor that keeps `proxy.py` alive.

Upstream, not mine: the [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) and the Chrome/Brave browsers (Chromium team), and the [`websockets`](https://github.com/python-websockets/websockets) and [`websocket-client`](https://github.com/websocket-client/websocket-client) Python libraries. See [CREDITS.md](CREDITS.md).

---

## 🛑 Two agents, one browser?

If you just ran a second browser agent and watched it **navigate a tab out from under the first one** — or reach for "the active tab" and grab the page *you* were reading — that is what this fixes.

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-broker/main/get.sh | bash
```

Nothing else changes. Your tools keep dialing port 9222 exactly as they do today.

The Chrome DevTools Protocol has **no concept of ownership**. Every agent sees every tab, including yours, and none of them can tell that another one is already working in the tab it just grabbed. The usual advice is "run them on different ports," which means a different browser, which means losing the logins that made your real browser worth driving in the first place.

browser-broker adds the missing layer: a process that owns the debug port and hands each agent an **exclusive lease** on its own tab.

---

## <a name="it-is-port-9222"></a>🔌 It *is* port 9222

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

---

## 🐍 Or use it directly

```python
from broker_client import lease

with lease("my-agent", url="https://mail.google.com", purpose="inbox sweep") as tab:
    tab.eval("document.title")   # you, alone, in a tab nobody else can touch
```

The lease auto-renews while you work and releases on exit, including on an exception. A crashed agent frees its tab instead of stranding it.

---

## 🧠 Why it works

**Agent tabs are ordinary background tabs.** In your window, behind what you are looking at. No second window, nothing parked off-screen, nothing headless — headless can't use the profile that has your logins, and a stray window just shoves yours aside. Isolation was never the window's job: the ownership registry and the swallowed focus commands do all of it.

**The rule is deliberately dumb.** Agents only ever get tabs the broker opened. Anything *you* opened is invisible to every agent, permanently, with no heuristics. Focus detection was rejected on purpose — window checks false-negative across macOS Spaces, and a heuristic that is wrong once is worse than a rule that is boring. The proxy has no way around this: its `/adopt` answers `410`. Only the older standalone [`broker.py`](broker.py) can adopt an existing tab, and only when asked by name.

**Leases expire.** 300 s by default, auto-renewed every 60 s by the client, reaped every 5 s. No deadlocks.

**It reconnects.** Browsers auto-update, crash, get quit. If the proxy's control socket to the browser drops, the next call opens a fresh one, and the LaunchAgent restarts the proxy if it exits.

**It has a heartbeat.** One log line an hour. A silent daemon is indistinguishable from a dead one.

---

## <a name="the-stage"></a>🎦 The stage: when an agent *does* want the screen

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
the link and move on rather than fight. Re-showing a page closes the stale copy first, so there
is never a row of half-current duplicates of the same page.

```
A: HTTP 200 after 0.7s   shown
B: HTTP 409 after 0.0s   stage held by agent-A for another 7.0s
C: HTTP 200 after 7.9s   waited, then shown
```

| | |
|---|---|
| `POST /show` | `{owner, url, purpose, wait, dwell}` → `{shown, target_id, held_for}`, or `409` with `held_by` and `seconds_left` |
| `POST /unstage` | drop the hold early |
| `GET /stage` | who has the screen and what is on it |

`BROKER_STAGE_DWELL` (20s, how long a shown page is protected) and `BROKER_STAGE_WAIT` (30s, how
long a caller queues) tune it. This is cooperative: it works because the polite path is one word
shorter than the rude one. On a machine running Claude Code you can make it binding with a
`PreToolUse` hook that denies `open -a <browser>` and points at `bb-show` instead;
[`hooks-example-focus-guard.py`](hooks-example-focus-guard.py) is one (its shebang and log path are
from the author's machine, so adjust them). If the broker is down, `bb-show` falls back to a plain
`open -a`, which is macOS only.

---

## 📡 API

| | |
|---|---|
| `POST /lease` | `{owner, url, purpose, ttl}` → `{lease_id, target_id, ws_url, expires_in}` |
| `POST /renew` | `{lease_id, ttl}` |
| `POST /release` | `{lease_id, close}` — closes the tab unless `close: false` |
| `POST /adopt` | returns `410` on the proxy. Only the older [`broker.py`](broker.py) supports it |
| `GET /status` | who holds what, for how long, and whether the browser is alive |
| `GET /health` | |

`ws_url` is a normal CDP page socket on the proxy's port (`ws://127.0.0.1:9222/devtools/page/<id>`). Drive it with the bundled client, Playwright's `connect_over_cdp`, or raw websockets. The traffic goes through the proxy, which is how it enforces who is allowed in.

Everything is configured by environment: `BROKER_PROXY_PORT` (9222, what tools dial), `BROKER_UPSTREAM_PORT` (9229, your real browser), `BROKER_PORT` (9223, lease API), `BROKER_TTL`, `BROKER_HEARTBEAT`, `BROKER_LOG`, and `BROKER_DEBUG=1` for a handshake trace.

---

## 🚀 Install

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-broker/main/get.sh | bash
```

Or by hand:

```bash
git clone https://github.com/nicedreamzapp/browser-broker && cd browser-broker
pip3 install websockets websocket-client
./launch-browser.sh   # your real profile, CDP on :9229
./install.sh          # LaunchAgent for proxy.py: :9222 for tools, :9223 lease API
curl localhost:9223/status
```

Already have something on 9222 you can't restart? Try it on another port first:
`BROKER_PROXY_PORT=9224 BROKER_UPSTREAM_PORT=9222 BROKER_PORT=9225 python3 proxy.py`

Python 3.9+. `proxy.py` needs [`websockets`](https://github.com/python-websockets/websockets) (13 or newer, for `websockets.asyncio`); `broker_client.py` and `broker.py` need `websocket-client`. `install.sh` picks one Python (`/usr/bin/python3` first, or `BROKER_PYTHON`), makes sure it has both, and writes its full path into the LaunchAgent, because launchd does not see your shell's PATH. Brave is the default; for Chrome: `BROWSER_APP="Google Chrome" ./launch-browser.sh`.

**Windows / Linux:** `proxy.py`, `broker.py` and `broker_client.py` are pure Python and don't care about the OS — only the two shell scripts are macOS (Windows has its own in `windows/`, below). Launch your browser with `--remote-debugging-port=9229 --remote-allow-origins=* --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling` (the same flags `launch-browser.sh` uses), then run `python proxy.py` from a startup task. `bb-show`'s fallback and the hook example are macOS only. Those backgrounding flags matter: agent tabs are background tabs, and without the flags Chrome throttles a background tab's timers to roughly once a second, which is enough to mount zero rows in a modern SPA. **What the flags do not do is make a hidden tab paint.** `requestAnimationFrame` does not fire in a document whose `visibilityState` is `hidden` — that is Page Visibility behaviour, not throttling, and no flag turns it back on. So a page that renders off timers or fetch callbacks fills in normally in an agent tab, and a page that renders inside a rAF loop (canvas, animation-driven charts) will sit at its initial frame. `Page.captureScreenshot` still returns a real PNG either way, so a screenshot is not proof the page is live. Developed on macOS. **Linux is verified for the browser-level CDP path** ([#1](https://github.com/nicedreamzapp/browser-broker/issues/1)): Ubuntu 26.04 / Chrome 153, two concurrent browser-level clients each saw only its own tabs, and cross-client `Target.attachToTarget` returned `No target with given id found` in both directions. Hidden-tab rendering was measured there too and reproduces on macOS: after 4s a hidden lease tab had fired 0 animation frames while its 100ms timer kept ticking, on both Ubuntu/Chrome 153 and macOS/Brave. Not yet covered there: a logged-in profile, Playwright `connectOverCDP`, the legacy `/json` first-page path, and multi-hour runs. **Windows is verified** (2026-10-02, Windows 11, Brave on Chromium 154, Python 3.14, logged-in real profile): `powershell -NoProfile -ExecutionPolicy Bypass -File windows\install.ps1` installs the two Python deps, registers a Task Scheduler task `browser-broker` (at logon, your user, interactive session, no time limit) and starts it. The task runs `windows\run-broker.ps1`, which runs `windows\launch-browser.ps1` once (exits if 9229 already answers, otherwise closes a CDP-less browser gracefully and relaunches it with the flags) and then keeps `proxy.py` alive under `pythonw` (no console window), restarting it 10s after any exit. Verified on that box: `/health` returned `{"ok": true, "brave": true}`, `/json/version` answered on 9222, a REST lease of a hidden tab evaluated `document.title` and closed on release, a legacy `/json` client and a raw browser-level client each saw only their own tab and never the human's, the stage opened a page in the human's window, and killing `proxy.py` brought it back on 9222/9223 within 10s. On Windows the stage relies on `Target.activateTarget` alone (the macOS app-activation step is skipped). Not covered yet: the close-and-relaunch path when the browser is already running without CDP, and a cold reboot. Logs: `launcher.log` and `broker.log` in the repo directory, `proxy.err` for Python tracebacks. If the browser is closed and reopened from its normal shortcut it comes back without CDP; run `windows\launch-browser.ps1` again (or sign out and back in).

---

## <a name="security"></a>🔒 Security

Localhost only, no auth. Anything that can reach `127.0.0.1:9223` can drive your logged-in browser — which is already true of `127.0.0.1:9222` the moment you enable remote debugging. Don't expose either.

---

## <a name="status"></a>📊 Status

Built in one night and proven with four manual tests: a leased tab loading Gmail authenticated while the human's visible tab sat on a different account; two agents running concurrently on separate targets; a simulated agent crash whose lease expired and was reclaimed; the human's tabs untouched throughout. Those manual runs are not checked in.

What is checked in is an automated suite in [`tests/`](tests/): 157 pytest tests that run the real `proxy.py`, `broker.py`, `broker_client.py`, `bb-show` and the hook example against a small fake Chrome DevTools endpoint ([`tests/fake_browser.py`](tests/fake_browser.py)) on loopback ports. It covers the ownership rules end to end (two clients blind to each other and to the human's tabs, focus stealers and `Browser.close` swallowed, tabs released on disconnect), the `/json` legacy path, the REST lease API, the stage queue, reaping and `tabs.json`. It needs no browser and no network, and runs on Linux in about 20 seconds. Two checks are macOS only (real `osascript`, `plutil`) and are skipped elsewhere. It does not drive a real Chrome, so the manual runs above still matter.

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Not yet built: a lane router that skips the browser entirely when a job is really an API call, and per-domain serialization so two agents never hammer one site as the same user.

---

## 🧩 The local-first stack

One piece of a set that runs entirely on your own machine, no cloud:

| Project | Role | What it does |
|---|---|---|
| 🧠 [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) | Brain | Claude Code on a local MLX model — the inference the agents think with |
| 🌐 [browser-agent](https://github.com/nicedreamzapp/browser-agent) | Hands | Drives your real browser via CDP — iframes, Shadow DOM, ProseMirror |
| 🚦 **browser-broker** | Traffic cop | Leases each agent its own tab so hands never collide, or grab yours |

---

## 🤝 Prior art and neighbours

This problem is being attacked from several directions right now. These aren't competitors so much as pieces of the same puzzle, and browser-broker borrows from the ideas in them:

- [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser) — `--cdp` mode with `--pin-tab` binds a session to one tab; [issue #1068](https://github.com/vercel-labs/agent-browser/issues/1068) asks for `Target.createBrowserContext` so parallel sessions get isolated cookies.
- [leeguooooo/chrome-use](https://github.com/leeguooooo/chrome-use) — per-session coloured tab groups in your real Chrome, never force-fronts a tab.
- [mediar-ai/playwright-mcp-orchestrator](https://github.com/mediar-ai/playwright-mcp-orchestrator) — one Chrome, many `@playwright/mcp` sessions, each scoped to its own tabs.
- [henu-wang/chrome-mcp-proxy](https://github.com/henu-wang/chrome-mcp-proxy) and `chrome-devtools-mcp-mux` — proxies in front of `chrome-devtools-mcp` that block focus-stealing and give each client its own tabs.
- [captivus/chrome-agent](https://github.com/captivus/chrome-agent), [pasky/chrome-cdp-skill](https://github.com/pasky/chrome-cdp-skill), [ofershap/real-browser-mcp](https://github.com/ofershap/real-browser-mcp) — drive your real, logged-in browser over CDP or an extension.

What browser-broker adds is a layer that sits *below* all of them. It doesn't care which tool the agent is — a CLI, an MCP server, Playwright, raw websockets — because it hands out plain CDP page sockets. The ownership rule and the transparent 9222 endpoint are the parts that don't exist elsewhere yet. If you maintain one of the projects above and want a lease layer under yours, open an issue.

---

## 📜 License

MIT — see [LICENSE](LICENSE). Built on other people's work — see [CREDITS.md](CREDITS.md).
