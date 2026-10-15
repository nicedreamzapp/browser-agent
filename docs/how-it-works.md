---
title: How browser-broker works, a CDP proxy with tab ownership
description: >-
  The architecture of browser-broker: a transparent Chrome DevTools Protocol proxy
  that tracks which client created each tab, filters Target events, swallows focus
  and browser-close commands, expires leases, and queues access to the screen.
---

# How it works

Everything on this page is read from `proxy.py`, the file the installer runs.

## The layout

```
tools ──▶ 127.0.0.1:9222  front door (asyncio TCP server)
                │  plain HTTP: answered here (/json, /json/new, /json/version ...)
                │  websocket upgrade: piped byte for byte to ──▶ inner websocket server
                │                                                (loopback, random port)
                ▼
          Registry: which tab belongs to which owner
                │
                ▼
          your browser 127.0.0.1:9229 (real profile)

scripts ──▶ 127.0.0.1:9223  REST API (lease, renew, release, show, status, stage)
```

The front door exists because the `websockets` library rejects any HTTP method
other than GET before user code runs, and Chrome 111+ clients send `PUT /json/new`.
So `proxy.py` reads the request head itself, answers plain HTTP directly, and splices
websocket upgrades through to an inner `websockets` server on an ephemeral loopback port.

The proxy also keeps one control websocket of its own to the browser
(`Upstream`). It uses it to create and close tabs and to list targets. If that
socket drops, any waiting calls fail at once and the next call reconnects.

## Ownership

The rule is simple on purpose: **the proxy only hands out tabs it created.** It
never lists, attaches to, or closes a tab it didn't make. Your tabs are invisible
to every agent, with no detection heuristics. The README explains why focus
detection was rejected: window checks give false negatives across macOS Spaces.

The `Registry` maps each target id to an owner and a kind:

| Kind | Who | Lifetime |
|---|---|---|
| `ws` | A browser-level websocket client. Owner id is `ws:<8 hex chars>`, one per connection. | Closed when that connection ends. |
| `pool` | A legacy HTTP client via `GET /json` or `/json/new`. | Closed when its page socket closes, or after 60 s if nobody ever attaches. |
| `rest` | A `POST /lease` from the REST API. | Closed on `/release`, or when the TTL passes without a `/renew`. |

Each tab is created with `Target.createTarget` and `background: true`: an ordinary
background tab in the window you already have open. Ownership is registered before
the reply goes back, because Chrome announces new targets to auto-attached clients
right away and the filter has to know the tab is theirs.

## Filtering a browser-level client

For each browser-level client, `proxy.py` opens its own upstream browser socket and
pumps messages both ways through two filters.

Client to browser:

- Commands about a target id (`attachToTarget`, `closeTarget`, `detachFromTarget`,
  `getWindowForTarget`) are refused with Chrome's own `No target with given id found`
  if the target isn't this client's.
- `getTargets` and `createTarget` are answered by the proxy itself.
- Focus commands and browser-wide commands are answered with an empty success.

Browser to client:

- `Target.attachedToTarget` for a target that isn't this client's (and isn't nested
  under one of its sessions) is dropped, and the proxy detaches that session upstream.
- Session traffic (`sessionId`) is passed only for sessions this client owns.
- `targetCreated`, `targetInfoChanged` and `targetDestroyed` are passed only for
  this client's targets. A `seen` set makes sure the client still hears
  `targetDestroyed` for its own tab after the registry has forgotten it.

There is a small race to handle: Chrome may announce a new target before the
proxy's `createTarget` reply comes back. While this client has a create in flight,
the filter waits up to about 1 second for the registry to catch up. Otherwise it
decides at once, so someone else's tab never stalls the event stream.

If an upstream call fails while handling a command, that one command gets a
`browser-broker: ...` error and the session stays up.

## Page sockets

A connection to `/devtools/page/<id>` is accepted only for a tab in the registry.
A pool tab accepts one socket. Messages are passed through untouched except for the
focus and browser-wide commands. When the socket closes, a pool tab is closed; a
REST lease tab stays until its lease ends.

## Leases and the reaper

Every 5 seconds a background task:

1. trims `launchd.err` / `proxy.err` if they are plain files over 10 MB;
2. closes REST tabs past their expiry and pool tabs never attached within 60 s;
3. drops bookkeeping for tabs that no longer exist in the browser (for example after a browser restart);
4. writes a heartbeat line once per `BROKER_HEARTBEAT` seconds, so a silent log means the proxy is dead.

## Surviving restarts

The proxy writes the ids of every tab it owns to `tabs.json` (atomically, via
`tabs.json.tmp`). On startup it closes any of those ids that are still open in the
browser, so a crash doesn't leave orphaned agent tabs in your window. Tabs the
browser itself restores after a browser restart come back with new ids and can't be
told apart from yours, so they are left alone.

At startup the proxy needs the browser to answer on the upstream port. If it doesn't,
it logs `no browser answering CDP on 9229 ... launch it with --remote-debugging-port=9229 first`
and exits with code 1, and the service manager restarts it.

## The stage

Everything above keeps agents out of your way. The stage is the one deliberate
exception: a way for an agent to put a page in front of you, one agent at a time.

It exists because `open -a "Brave Browser" <url>` and AppleScript `activate` are
macOS calls, not CDP, so the proxy can't see or block them. Two Claude Code sessions
running `open -a` minutes apart threw the window back and forth while the proxy log
stayed clean.

`POST /show` (or `bb-show`, or `broker_client.show()`):

1. waits up to `wait` seconds (default 30) for the stage lock; if it doesn't get it,
   returns `409` with who holds it and for how long;
2. closes **this owner's** previous stage tab, if any. Never another owner's;
3. opens the URL in a foreground tab, calls `Target.activateTarget`, and on macOS runs
   `osascript -e 'tell application "<BROWSER_APP>" to activate'`;
4. returns at once and holds the lock for `dwell` seconds (default 20), then releases it.

If showing fails, the lock is released immediately so later callers don't get stuck
on `409`. `POST /unstage` releases it early. On Windows only `Target.activateTarget`
is used.

The stage is cooperative. It works because `bb-show` is shorter to type than
`open -a "Brave Browser"`. To enforce it in Claude Code, use the example `PreToolUse`
hook described in [Configuration](configuration.md#claude-code-hook-example).

## Hidden tabs and rendering

Agent tabs are background tabs, so their `visibilityState` is `hidden`. Two things
follow, both measured in [issue #1](https://github.com/nicedreamzapp/browser-broker/issues/1):

- Without the `--disable-*backgrounding*` flags, Chromium throttles a background
  tab's timers to about once a second, which is enough to stop a modern single-page app from
  rendering any rows. The launch scripts pass those flags.
- With the flags, timers and fetch callbacks run normally, but
  `requestAnimationFrame` still never fires in a hidden document. That is Page
  Visibility behaviour and no flag changes it. In the test, a hidden lease tab fired
  0 animation frames in 4 seconds while its 100 ms timer kept ticking, on both
  Ubuntu/Chrome 153 and macOS/Brave. `Page.captureScreenshot` still returned a real
  PNG, so a screenshot is not proof the page is live.

## Security model

Localhost only, no authentication. Anything that can reach `127.0.0.1:9223` can
drive your logged-in browser, which is already true of a browser with remote
debugging on. Don't expose either port. The ownership filter keeps cooperating
tools apart; it is not a sandbox against a hostile local process, which could talk
to port 9229 directly.
