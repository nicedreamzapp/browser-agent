---
title: browser-broker quickstart, give each AI agent its own tab
description: >-
  Get started with browser-broker: point existing CDP tools at port 9222, lease a
  private tab from Python with broker_client.lease(), use the REST API, and show a
  page to the human with bb-show.
---

# Quickstart

This assumes the proxy is running (see [Install](install.md)) and
`curl localhost:9223/health` returns `{"ok": true, ...}`.

## 1. Point your existing tools at 9222

Nothing changes. Whatever you already run against port 9222 keeps working, and
now only sees its own tabs:

```bash
agent-browser --cdp 9222
chrome-devtools-mcp --browser-url http://127.0.0.1:9222
```

```python
# Playwright
browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
```

What each kind of client gets:

- **Browser-level websocket** (Playwright, Puppeteer, `chrome-devtools-mcp`,
  anything that reads `webSocketDebuggerUrl` from `/json/version`): one owner for as
  long as the socket stays open. If it asks for targets before creating any, it is
  given one fresh `about:blank` tab so "use the first page" works. All its tabs close
  when it disconnects.
- **Legacy `/json` client** (`GET /json`, then connect to a page socket): `/json`
  lists exactly one unclaimed tab made for it. That tab is reserved for 10 seconds,
  and only one page socket may attach. It closes when that socket closes.

## 2. Or lease a tab from Python

Copy `broker_client.py` next to your agent (it needs `websocket-client`):

```python
from broker_client import lease

with lease("my-agent", url="https://mail.google.com", purpose="inbox sweep") as tab:
    print(tab.eval("document.title"))
    tab.goto("https://example.com")
    tab.send("Page.reload")
```

- `lease(owner, url=None, purpose=None, hidden=True, ttl=300)` opens a background
  tab and yields a `Tab`.
- The client renews the lease every 60 seconds while the block runs, retrying a
  failed renew up to 5 times with backoff, and stops only when the broker answers 404.
- On exit, including on an exception, it closes the socket and releases the lease,
  which closes the tab. If the release fails, the server reaps the tab when the TTL runs out.

`Tab` methods:

| Method | What it does |
|---|---|
| `send(method, **params)` | Send any CDP command and wait for its reply. Raises `RuntimeError` on a CDP error. |
| `goto(url, wait=3)` | `Page.enable`, `Page.navigate`, then sleeps `wait` seconds. |
| `eval(expr)` | `Runtime.evaluate` with `returnByValue` and `awaitPromise`; returns the value. |
| `close()` | Close the websocket. |

## 3. Or use the REST API directly

```bash
curl -s -X POST localhost:9223/lease \
  -H 'Content-Type: application/json' \
  -d '{"owner":"my-agent","url":"https://example.com","ttl":300}'
# {"lease_id": "...", "target_id": "...", "ws_url": "ws://127.0.0.1:9222/devtools/page/...", "expires_in": 300}
```

Connect to `ws_url` with any CDP client. Renew before the TTL runs out with
`POST /renew {"lease_id": "..."}` and finish with `POST /release {"lease_id": "..."}`.
Full reference: [Configuration](configuration.md#rest-api-port-9223).

## 4. Showing a page to the human

Agents should not run `open -a "Brave Browser" <url>`. That goes straight to macOS,
around the proxy, so two agents doing it throw the window back and forth. Use the stage:

```bash
bb-show https://example.com my-agent "the report you asked for"
```

| Exit code | Meaning |
|---|---|
| `0` | Shown. |
| `2` | Another agent has the screen. Print the link and move on; don't force it. |
| `1` | Error (no URL given, or the fallback `open -a` failed). |

**Close every tab you opened when the task is done.** Before reporting back, close what you showed:

```bash
bb-close my-agent
```

From Python:

```python
from broker_client import show, stage

show("https://example.com", owner="my-agent", purpose="report")
print(stage())   # who has the screen right now
```

`show()` raises `RuntimeError("screen busy: ...")` if the screen stayed busy past `wait`.

To make this binding for Claude Code, see the example `PreToolUse` hook in
[Configuration](configuration.md#claude-code-hook-example).

## 5. See what is going on

```bash
curl -s localhost:9223/status   # every broker tab: owner, kind, attached, expires_in, lease_id
curl -s localhost:9223/stage    # who holds the screen
tail -f broker.log              # one line per tab opened/closed, plus an hourly heartbeat
```
