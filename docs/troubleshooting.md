---
title: browser-broker troubleshooting, errors and fixes
description: >-
  Fixes for common browser-broker problems: "no browser answering CDP", "not your
  tab", "No target with given id found", 409 screen busy, 504 timeouts, blank
  hidden tabs, crash-looping LaunchAgent, and Brave opening without remote debugging.
---

# Troubleshooting

Each entry quotes the message the code actually prints, so you can search for it.

## The proxy will not start

### `no browser answering CDP on 9229 (...) — launch it with --remote-debugging-port=9229 first`

`proxy.py` could not fetch `http://127.0.0.1:9229/json/version` at startup and
exited with code 1. Your browser is not running with remote debugging on the
upstream port.

- macOS: `./launch-browser.sh` (it prints `browser up with CDP on 9229` on success).
- Windows: run `windows\launch-browser.ps1`.
- Linux: start the browser with the flags on the [Install](install.md#linux-and-any-other-os) page.

Check with `curl http://127.0.0.1:9229/json/version`.

### `browser did not come up on 9229`

`launch-browser.sh` quit and relaunched the browser but nothing answered on the
port 6 seconds later. Try running it again. If the browser was already running
and didn't quit, close it yourself first. Chrome users need
`BROWSER_APP="Google Chrome"`.

### `could not get websockets>=13 into any python3; set BROKER_PYTHON=/path/to/python3`

`install.sh` tried `$BROKER_PYTHON`, `/usr/bin/python3` and the `python3` on your
PATH, and could not import `websockets` 13+ and `websocket` in any of them, even
after `pip install --user`. Point `BROKER_PYTHON` at a Python where
`pip install "websockets>=13" websocket-client` works.

The reason `install.sh` is strict: installing the packages into one Python while
launchd ran another left a fresh install crash-looping every 30 seconds.

### `broker did not answer — is the browser up? try ./launch-browser.sh`

`install.sh` loaded the LaunchAgent but `/health` on 9223 did not answer 3 seconds
later. Usually the browser isn't up on 9229 (see above). Look at `launchd.err` in
the repo directory for the Python traceback.

### `ImportError` / `ModuleNotFoundError` for `websockets.asyncio`

`proxy.py` imports `websockets.asyncio`, which needs `websockets` 13 or newer.
`pip install --upgrade "websockets>=13"` in the Python that runs the proxy.

### Port 9222 is already in use

Something else (often a browser started with `--remote-debugging-port=9222`) holds
the port. Relaunch the browser on 9229, or test the proxy on spare ports:
`BROKER_PROXY_PORT=9224 BROKER_UPSTREAM_PORT=9222 BROKER_PORT=9225 python3 proxy.py`.

### `no pythonw.exe next to ...` (Windows)

`windows\install.ps1` needs `pythonw.exe` in the same folder as the chosen
`python.exe`. Set `$env:BROKER_PYTHON` to a full CPython install that has both.

### `no brave.exe/chrome.exe found; set BROWSER_EXE` (Windows)

`launch-browser.ps1` checks the usual Brave and Chrome install folders. Set
`$env:BROWSER_EXE` to your browser's full path.

### `broker did not answer on 9223 yet; see launcher.log and proxy.err` (Windows)

The scheduled task started but `/health` didn't answer within 40 seconds. Read
`launcher.log` and `proxy.err` in the repo directory.

## Agents lose the browser

### Brave was opened from the Dock or taskbar and agents went blind

A browser opened the normal way has no remote debugging port, so `/health` reports
`"brave": false` and the proxy can't open tabs. Relaunch it with
`launch-browser.sh` / `launch-browser.ps1`.

On Windows, `run-broker.ps1` does this automatically for a `brave.exe` that is
under 90 seconds old. On macOS, `brave-cdp-guard.sh` does the same job (see
[Install](install.md#optional-brave-cdp-guardsh)). A browser older than 90 seconds
is left alone on purpose so a window you are using is never yanked; the macOS
guard logs `too old to restart safely, left alone`.

### `upstream control socket dropped: ...` in broker.log

The proxy's own connection to the browser closed, usually because the browser
restarted or updated. The next call reconnects. Tabs that disappeared are
dropped from the registry on the next reaper pass.

## Errors your CDP client sees

### Websocket closed with `1008 not your tab`

The client connected to `/devtools/page/<id>` for a tab the proxy didn't make (one
of yours, or an id from before a restart). Get a tab from the proxy: `GET /json`,
`/json/new`, a browser-level `Target.createTarget`, or `POST /lease`.

### Websocket closed with `1008 tab already in use`

A tab from `GET /json` or `/json/new` accepts one page socket. A second client got
the same id. Each client should ask for its own.

### Websocket closed with `1011 browser unreachable: ...`

The proxy couldn't open its upstream socket to the browser. Check that the
browser is still up on 9229.

### `No target with given id found`

Returned for `Target.attachToTarget`, `closeTarget`, `detachFromTarget` or
`Browser.getWindowForTarget` on a tab this client doesn't own. This is the
isolation working. If the client expected to see an existing tab (one you opened),
that is by design: agents can only use tabs the proxy made for them.

### `browser-broker: browser target is not shared`

The client sent `Target.attachToBrowserTarget`. The proxy doesn't allow it.
TODO-VERIFY: which client libraries send this and whether they fall back cleanly.

### `browser-broker: <error>`

An upstream call failed while handling one command. Only that command fails; the
session stays connected.

### Puppeteer's `browser.close()` didn't close the browser

Correct. `Browser.close` is swallowed; it ends that client's connection and closes
its tabs. The log line is `ws:xxxx sent Browser.close -- swallowed, the browser is the human's`.

### An agent called `bringToFront` and nothing happened

`Target.activateTarget`, `Page.bringToFront` and `/json/activate` are answered with
success and never forwarded. Use the [stage](how-it-works.md#the-stage) to show a page.

## REST API errors

| Status | Body | Cause |
|---|---|---|
| `404` | `unknown lease` | `/renew` or `/release` for a lease that was released, expired, or never existed. `broker_client.lease()` stops renewing when it sees this. |
| `400` | `show needs a url` | `/show` without `url`. |
| `409` | `stage held by <owner> for another <n>s` | Another agent holds the screen. Print the link and move on. |
| `410` | `adopt is not supported by the proxy ...` | The proxy never hands out tabs it didn't create. Only `broker.py` has `/adopt`. |
| `504` | `browser did not answer in time` | The call took longer than 30 s (or `wait + 15` for `/show`). |
| `500` | `<ExceptionType>: <message>` | Anything else, for example `ConnectionError: browser control socket dropped`. |

`bb-show` prints `screen busy — ...` and exits 2 on `409`. On anything else other
than `200` it prints `broker not answering (code=...) — falling back to a raw open`
and runs `open -a`.

## Pages look blank in an agent tab

Agent tabs are hidden background tabs. Pages that render on timers or fetch
callbacks fill in normally, as long as the browser was launched with the
`--disable-*backgrounding*` flags. Pages that draw inside a `requestAnimationFrame`
loop (canvas, animated charts) stay on their first frame, because rAF never fires
in a hidden document. `Page.captureScreenshot` still returns a valid PNG, so don't
treat a screenshot as proof the page rendered. Details and measurements:
[How it works](how-it-works.md#hidden-tabs-and-rendering) and
[issue #1](https://github.com/nicedreamzapp/browser-broker/issues/1).

There is no "this tab must really render" lease option yet. In issue #1 the author
describes one as a possible future addition; it has not been built.

## Log files

| File | Written by | Notes |
|---|---|---|
| `broker.log` | `proxy.py` | Rolls to `broker.log.1` past 5 MB. |
| `tabs.json` | `proxy.py` | Ids of tabs the proxy owns, for cleanup after a crash. |
| `launchd.err` | launchd | Python stderr on macOS. Emptied past 10 MB. |
| `proxy.err` | `run-broker.ps1` | Python stderr on Windows. |
| `launcher.log` | Windows scripts | Browser launches and proxy restarts. |
| `brave-cdp-guard.log` | `brave-cdp-guard.sh` | Relaunch decisions on macOS. |

Set `BROKER_DEBUG=1` before starting `proxy.py` to see websocket handshake details.
