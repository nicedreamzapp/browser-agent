---
title: Install browser-broker on macOS, Windows or Linux
description: >-
  How to install browser-broker: the one-line macOS installer, a manual install,
  the Windows Task Scheduler installer, and running proxy.py on Linux with the
  right Chrome or Brave flags.
---

# Install

## Requirements

- Python 3.9 or newer.
- `proxy.py` needs [`websockets`](https://github.com/python-websockets/websockets)
  13 or newer (it imports `websockets.asyncio`).
- `broker_client.py` and `broker.py` need
  [`websocket-client`](https://github.com/websocket-client/websocket-client).
- Chrome or Brave. Brave is the default in the launch scripts.

## macOS: one line

```bash
curl -fsSL https://raw.githubusercontent.com/nicedreamzapp/browser-broker/main/get.sh | bash
```

What `get.sh` does, in order:

1. If something already answers `http://127.0.0.1:${BROKER_PORT:-9223}/health`, it
   prints the status and stops. It will not install a second broker on top of a
   working one.
2. Checks that `git` and `python3` exist.
3. Clones the repo into `$BROWSER_BROKER_DIR` (default `~/.browser-broker`), or
   runs `git pull --ff-only` if it is already there.
4. On macOS: runs `./launch-browser.sh` (quits and relaunches your browser with
   CDP on 9229 and your last session restored), then `./install.sh`.
5. On anything else: prints the browser flags and tells you to run `proxy.py`
   yourself.

`install.sh` picks one Python (`$BROKER_PYTHON`, then `/usr/bin/python3`, then
whatever `python3` is on your PATH), installs `websockets>=13` and
`websocket-client` into it with `pip install --user` if needed, writes that
Python's full path into `~/Library/LaunchAgents/com.browser-broker.plist`, and
loads it with `launchctl`. launchd does not see your shell's PATH, which is why
the full path is written in.

The LaunchAgent starts `proxy.py` at login and restarts it if it exits with an
error (`KeepAlive` / `SuccessfulExit: false`, `ThrottleInterval` 30 seconds).
Python's stderr goes to `launchd.err` in the repo directory.

## macOS: by hand

```bash
git clone https://github.com/nicedreamzapp/browser-broker && cd browser-broker
pip3 install websockets websocket-client
./launch-browser.sh   # your real profile, CDP on :9229
./install.sh          # LaunchAgent for proxy.py: :9222 for tools, :9223 lease API
curl localhost:9223/status
```

For Chrome instead of Brave:

```bash
BROWSER_APP="Google Chrome" ./launch-browser.sh
```

### Optional: brave-cdp-guard.sh

If Brave is opened the normal way (Dock icon, a link from another app, an
update relaunch), it comes up without remote debugging and every agent loses the
browser. `brave-cdp-guard.sh` checks for that. It only acts on a Brave process
under 90 seconds old that is not answering on port 9229, relaunches it through
`launch-browser.sh`, and restarts the broker LaunchAgent. It acts at most once
every 5 minutes and logs to `brave-cdp-guard.log`.

The repo comments say it runs every 10 seconds, but no LaunchAgent for it is
shipped. TODO-VERIFY: how this script is meant to be scheduled on macOS. It
ignores Brave processes started with `--user-data-dir`, and it hardcodes port 9229
and the app name `Brave Browser`.

## Windows

Verified on Windows 11 with Brave (Chromium 154) and Python 3.14, per the README.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File windows\install.ps1
```

`windows\install.ps1`:

- picks a Python (`$env:BROKER_PYTHON`, then `py -3`, then `python.exe` on PATH)
  and requires a `pythonw.exe` next to it;
- installs `websockets` and `websocket-client` with `pip install --user`;
- writes the `pythonw.exe` path to `windows\pythonw.txt`;
- registers a Task Scheduler task named `browser-broker` (or `$env:BROKER_TASK`)
  that runs at logon, as you, in your interactive session, with no time limit;
- starts it and waits up to 40 seconds for `http://127.0.0.1:9223/health`.

The script comment says "Pick a Python 3.10+", but it does not check the version.
TODO-VERIFY: the minimum Python version on Windows.

The task runs `windows\run-broker.ps1`, which:

1. runs `windows\launch-browser.ps1` once. That script exits if port 9229 already
   answers; otherwise it finds `brave.exe` or `chrome.exe` (or `$env:BROWSER_EXE`),
   closes a running copy gracefully, and relaunches it with the flags;
2. starts `proxy.py` under `pythonw` (no console window) and restarts it 10 seconds
   after any exit;
3. every 10 seconds, if a `brave.exe` under 90 seconds old is running without CDP
   on 9229, relaunches it with CDP on (at most once every 5 minutes).

Logs: `launcher.log` and `broker.log` in the repo directory, `proxy.err` for
Python tracebacks.

## Linux (and any other OS)

`proxy.py`, `broker.py` and `broker_client.py` are plain Python. Start your
browser with the same flags `launch-browser.sh` uses:

```bash
google-chrome \
  --remote-debugging-port=9229 --remote-allow-origins=* \
  --disable-backgrounding-occluded-windows --disable-renderer-backgrounding \
  --disable-background-timer-throttling
```

Then run the proxy, for example from a startup task or a systemd user unit:

```bash
pip3 install "websockets>=13" websocket-client
python3 proxy.py
```

No service file for Linux ships in the repo. `bb-show`'s fallback and the hook
example use `open -a`, which is macOS only.

## Trying it next to something already on 9222

If you can't free port 9222 yet, run the proxy on other ports first:

```bash
BROKER_PROXY_PORT=9224 BROKER_UPSTREAM_PORT=9222 BROKER_PORT=9225 python3 proxy.py
```

## Check it

```bash
curl localhost:9223/health     # {"ok": true, "brave": true}
curl localhost:9223/status     # tabs, leases, browser alive
curl localhost:9222/json/version
```

The `brave` field in `/health` is true for any browser answering on the upstream
port, Chrome included.

## Platform support

| Platform | What has been checked | Source |
|---|---|---|
| macOS + Brave | Developed here. Four manual tests: a leased tab loaded Gmail logged in while the human's tab was on another account; two agents concurrently on separate targets; a simulated crash whose lease expired and was reclaimed; the human's tabs untouched. | README "Status" |
| Linux | Ubuntu 26.04 / Chrome 153: two concurrent browser-level clients each saw only their own tabs; cross-client `Target.attachToTarget` returned `No target with given id found` both ways. Not covered: logged-in profile, Playwright, legacy `/json`, multi-hour runs. | [issue #1](https://github.com/nicedreamzapp/browser-broker/issues/1) |
| Windows 11 + Brave | `/health`, `/json/version`, a REST lease, a legacy `/json` client and a raw browser-level client each isolated, the stage, and restart within 10 s of killing `proxy.py`. Not covered: close-and-relaunch when the browser already runs without CDP, a cold reboot. | README "Windows / Linux" |

## Uninstall

No uninstall script ships. On macOS the LaunchAgent can be removed with:

```bash
launchctl bootout "gui/$(id -u)/com.browser-broker"
rm ~/Library/LaunchAgents/com.browser-broker.plist
```

On Windows, remove the scheduled task (`Unregister-ScheduledTask -TaskName browser-broker`).
TODO-VERIFY: these uninstall steps are derived from how the installers register
the services, not from a tested uninstall.
