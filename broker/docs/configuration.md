---
title: browser-broker configuration, env vars, ports and REST API
description: >-
  Every browser-broker setting read from the source: environment variables for
  proxy.py, broker.py, broker_client.py and the install scripts, the REST lease
  API on port 9223, the CDP endpoints on 9222, and the fixed internal timeouts.
---

# Configuration

There are no command-line flags and no config file. Everything is set with
environment variables, read once at startup. This page lists every one the code
reads, file by file.

## proxy.py (what the installer runs)

| Variable | Default | What it does |
|---|---|---|
| `BROKER_PROXY_PORT` | `9222` | Port the CDP front door listens on. This is what your tools dial. |
| `BROKER_UPSTREAM_PORT` | `9229` | Port your real browser's remote debugging listens on. |
| `BROKER_PORT` | `9223` | Port for the REST lease API and the stage. |
| `BROKER_TTL` | `300` | Default lease TTL in seconds for `POST /lease` and `POST /renew` when the body has no `ttl`. |
| `BROKER_HEARTBEAT` | `3600` | Seconds between heartbeat lines in the log. |
| `BROKER_LOG` | `broker.log` next to `proxy.py` | Log file path. `~` is expanded. `tabs.json` is written in the same directory. |
| `BROKER_STAGE_DWELL` | `20` | Seconds a page shown through the stage is protected from the next `/show`. |
| `BROKER_STAGE_WAIT` | `30` | Seconds a `/show` caller queues before getting `409`. |
| `BROWSER_APP` | `Brave Browser` | macOS app name the stage activates with `osascript` after showing a page. |
| `BROKER_DEBUG` | unset | Any non-empty value turns on Python `DEBUG` logging, which includes the `websockets` handshake trace. When unset, `websockets.server` handshake errors are silenced. |

All ports bind to `127.0.0.1` only.

### Fixed values in proxy.py

These are constants in the code, not settings:

| Constant | Value | Meaning |
|---|---|---|
| `UNATTACHED_TTL` | 60 s | A tab made for a legacy `/json` client that never attaches is closed after this. |
| `POOL_HOLD` | 10 s | A tab listed by `GET /json` is reserved for that caller for this long. |
| Reaper interval | 5 s | How often expired leases and unattached tabs are swept. |
| Upstream reply timeout | 20 s | How long the proxy waits for the browser to answer one command. |
| REST call timeout | 30 s | How long a REST request waits on the proxy's event loop before `504`. `/show` uses `wait + 15`. |
| `LOG_MAX` | 5 MB | `broker.log` is renamed to `broker.log.1` past this. |
| `STDIO_MAX` | 10 MB | If stdout or stderr is a plain file (launchd / Task Scheduler) past this, it is emptied. |
| `FOCUS_STEALERS` | `Target.activateTarget`, `Page.bringToFront` | Answered with success, never forwarded. |
| `BROWSER_KILLERS` | `Browser.close`, `Browser.crash`, `Browser.crashGpuProcess`, `Browser.setWindowBounds` | Answered with success, never forwarded. |

## broker_client.py

| Variable | Default | What it does |
|---|---|---|
| `BROKER_URL` | `http://127.0.0.1:9223` | Where the REST API is. |
| `BROKER_OWNER` | unset | Owner name `show()` uses when you don't pass `owner`. Falls back to `anonymous`. |

Fixed: renew every 60 s, default `ttl` 300 s. `_post` uses a 15 s HTTP timeout.
`Tab` connects with a 30 s socket timeout.

## bb-show

```
bb-show <url> [owner] [purpose]
```

| Variable | Default | What it does |
|---|---|---|
| `BROKER_URL` | `http://127.0.0.1:9223` | REST API base. |
| `BROKER_OWNER` | unset | Owner when the second argument is missing. |
| `CLAUDE_SESSION_ID` | unset | If `BROKER_OWNER` is unset, the owner is `basename` of this; else `agent`. |
| `BROWSER_APP` | `Brave Browser` | App for the fallback `open -a` when the broker does not answer. |

The request to `/show` has a 60 second `curl` timeout. Any status other than 200
or 409 (including no answer at all) falls back to `open -a`, which only works on macOS.
On success it prints a second line reminding the caller to run `bb-close` when done.

## bb-close

```
bb-close <owner|url-substring>
```

**Close every tab you opened when the task is done.** `bb-close` posts `{"match": ...}` to `/unshow`, which
closes every stage tab whose owner equals the argument or whose current URL contains it. Only
tabs the stage itself opened are closed; a human tab that `bb-show` refreshed in place is left
open and reported as `left open`. Exit `0` closed something, `3` nothing matched, `1` usage error
or broker down (it never falls back to closing tabs on 9229 directly, since it cannot tell
which of those are the human's). Uses `BROKER_URL` like `bb-show`.

## broker.py (legacy, not run by the installer)

| Variable | Default | What it does |
|---|---|---|
| `BROKER_CDP_PORT` | `9222` | The browser's CDP port. broker.py talks to the browser directly, with no proxy in front. |
| `BROKER_PORT` | `9223` | REST API port. |
| `BROKER_TTL` | `300` | Default lease TTL. |
| `BROKER_HEARTBEAT` | `3600` | Seconds between heartbeat lines. |
| `BROKER_LOG` | `broker.log` next to `broker.py` | Log file. |

broker.py serves `/lease`, `/adopt`, `/renew`, `/release`, `/status`, `/health`.
It has no stage and no CDP filtering: a lease's `ws_url` points straight at the
browser.

## Install and launch scripts

| Script | Variable | Default | What it does |
|---|---|---|---|
| `get.sh` | `BROWSER_BROKER_DIR` | `~/.browser-agent` (or an existing `~/.browser-broker`) | Where the repo is cloned. The broker runs from its `broker/` folder. |
| `get.sh`, `install.sh` | `BROKER_PORT` | `9223` | Port used for the "already installed?" and post-install health checks. |
| `install.sh` | `BROKER_PYTHON` | unset | Python to try first. |
| `launch-browser.sh` | `BROKER_UPSTREAM_PORT` | `9229` | Port passed to `--remote-debugging-port`. |
| `launch-browser.sh` | `BROWSER_APP` | `Brave Browser` | App to quit and relaunch, e.g. `Google Chrome`. |
| `windows\install.ps1` | `BROKER_TASK` | `browser-broker` | Scheduled task name. |
| `windows\install.ps1` | `BROKER_PYTHON` | unset | Python to use. |
| `windows\launch-browser.ps1` | `BROKER_UPSTREAM_PORT` | `9229` | Remote debugging port. |
| `windows\launch-browser.ps1` | `BROWSER_EXE` | first found of Brave, then Chrome | Browser executable. |
| `windows\run-broker.ps1` | `BROKER_PYTHONW` | contents of `windows\pythonw.txt`, else `pythonw.exe` | Interpreter for `proxy.py`. |

!!! warning "Settings the services do not pass on"
    The macOS LaunchAgent plist sets no environment variables, so `proxy.py` run by
    launchd always uses the defaults (9222 / 9229 / 9223). Setting `BROKER_PORT`
    when running `install.sh` only changes which port the script checks afterwards.
    On Windows, `run-broker.ps1` checks port 9229 and `install.ps1` checks port 9223
    with hardcoded values. TODO-VERIFY: whether non-default ports are meant to be
    supported under the installed services.

Browser flags used by both launchers: `--remote-debugging-port=<port>`,
`--remote-allow-origins=*`, `--restore-last-session`,
`--disable-backgrounding-occluded-windows`, `--disable-renderer-backgrounding`,
`--disable-background-timer-throttling`.

## REST API (port 9223)

All bodies are JSON. Paths are matched by prefix.

| Endpoint | Body | Reply |
|---|---|---|
| `POST /lease` | `{owner, url, purpose, ttl, hidden}` | `{lease_id, target_id, ws_url, expires_in}` |
| `POST /renew` | `{lease_id, ttl}` | `{lease_id, expires_in}`, or `404 {"error": "unknown lease"}` |
| `POST /release` | `{lease_id}` | `{released}`, or `404` |
| `POST /show` | `{owner, url, purpose, wait, dwell}` | `{shown, target_id, held_for, owner}`; `409 {error, held_by, seconds_left, showing}` if busy; `400` without `url` |
| `POST /unstage` | none | `{freed: true/false}` |
| `POST /unshow` | `match` (owner or URL substring) | `{closed: [...], left_open: [...]}` |
| `POST /adopt` | any | Always `410` on the proxy |
| `GET /status` | | `{tabs: [...], count, brave_alive, leases: [...]}` |
| `GET /health` | | `{ok: true, brave: <browser answers>}` |
| `GET /stage` | | `{held_by, showing, purpose, seconds_left, tab, tabs}` |

Defaults: `owner` is `anonymous`, `url` is `about:blank`, `ttl` is `BROKER_TTL`.
A request that waits too long on the browser gets `504 {"error": "browser did not
answer in time"}`; any other exception gets `500`.

Notes on what the proxy code actually does:

- `POST /release` on the proxy **always closes the tab**. It ignores `close`. The
  README's "closes the tab unless `close: false`" describes `broker.py`.
  TODO-VERIFY: whether `close: false` should be honoured by the proxy.
- `hidden` is stored and shown in `/status`, but every tab is opened the same way
  (`background: true`). It changes nothing else in `proxy.py`.
- `/status` lists each lease's `purpose` as `null`, even though `/lease` stores it.
  TODO-VERIFY: likely an oversight.
- `/renew` resets the expiry from now; it does not add to the remaining time.
- `/unstage` frees whoever holds the stage, not only the caller.

## CDP endpoints (port 9222)

HTTP:

| Path | Behaviour |
|---|---|
| `/json/version` | The browser's own version info, with `(browser-broker)` appended to `Browser` and a fresh `webSocketDebuggerUrl` on the proxy port. A trailing slash is accepted. |
| `/json`, `/json/list` | One unclaimed tab for this caller, reserved for 10 s. Made fresh if none is free. |
| `/json/new?<url>` | GET or PUT. Opens a new pool tab at `<url>` (default `about:blank`). |
| `/json/activate/<id>` | `200 Target activated (noop)`. Nothing is forwarded. |
| `/json/close/<id>` | Closes the tab only if it is a pool tab (one made for a legacy `/json` client); `404` otherwise. REST and browser-client tabs can't be closed this way. |
| `/json/protocol` | Passed through from the browser. |

Websockets:

- `/devtools/browser/<anything>`: a new browser-level client with its own owner id.
- `/devtools/page/<id>`: only for tabs the proxy made. Unknown ids are closed with
  `1008 not your tab`; a pool tab that already has a socket gets `1008 tab already in use`.

Browser-level command handling:

| Command | What happens |
|---|---|
| `Target.getTargets` | Only this client's tabs. If it has none, one `about:blank` tab is made for it. |
| `Target.createTarget` | Made by the proxy as a background tab owned by this client. With auto-attach on, the reply waits (up to about 2 s) until the attach event has been sent. |
| `Target.attachToTarget`, `Target.closeTarget`, `Target.detachFromTarget`, `Browser.getWindowForTarget` | Error `No target with given id found` for any tab that isn't this client's. |
| `Target.attachToBrowserTarget` | Error `browser target is not shared`. |
| `Target.createBrowserContext` / `disposeBrowserContext` | Allowed; a client can only dispose contexts it created. Its contexts are disposed when it disconnects. |
| `Target.setDiscoverTargets`, `Target.setAutoAttach` | Forwarded; the resulting events are filtered to this client's targets. Other tabs that auto-attach are detached quietly. |
| Focus and browser-wide commands | See `FOCUS_STEALERS` and `BROWSER_KILLERS` above. `Browser.close` also closes this client's connection. |

On a page socket, only the focus and browser-wide commands are intercepted;
everything else goes straight to the tab.

## Claude Code hook example

`hooks-example-focus-guard.py` is a `PreToolUse` hook for Claude Code's Bash tool.
It denies commands matching:

- `open -a <browser>` (Brave, Google Chrome, Chromium, Safari),
- AppleScript that tells one of those browsers to `activate`, set the active tab,
  make a new tab or set a window index,
- raw `/json/activate/`, `Target.activateTarget` or `Page.bringToFront`,

and tells the agent to use `bb-show` instead. Commands that already mention
`bb-show` or the `/show` endpoint on 9223 are let through. Each decision is
appended to `~/.claude/browser-focus-audit.log`.

Its shebang (`#!/opt/homebrew/bin/python3.12`) and its message text come from the
author's machine; adjust them before use. TODO-VERIFY: the exact `settings.json`
snippet for registering it; the repo does not include one.
