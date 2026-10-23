---
title: browser-broker credits and upstream projects
description: >-
  The upstream projects browser-broker is built on, the Chrome DevTools Protocol,
  websockets and websocket-client, the people who tested it, and related projects
  working on the same problem.
---

# Credits

browser-broker is designed and written by Matt Macosko (Nice Dreamz LLC), MIT licensed.
It is a thin layer on top of other people's work.

## Built on

| Project | What it does here | By |
|---|---|---|
| [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) | Every tab the broker opens, filters and closes | The Chromium team |
| Chrome and Brave | The browsers being shared | The Chromium team, Brave Software |
| [websockets](https://github.com/python-websockets/websockets) | The async websocket client and server inside `proxy.py` | Aymeric Augustin and contributors |
| [websocket-client](https://github.com/websocket-client/websocket-client) | The CDP socket in `broker_client.py` and `broker.py` | Hiroki Ohtani and contributors |
| [Claude Code](https://claude.ai/code) | The `PreToolUse` hook mechanism the focus-guard example plugs into; several commits were co-authored with it | Anthropic |
| [MkDocs](https://www.mkdocs.org/) and [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) | This documentation site | Tom Christie and contributors; Martin Donath and contributors |

## Field reports

| Who | What they found |
|---|---|
| [@Hronom](https://github.com/Hronom) | Verified the isolation on Linux in [issue #1](https://github.com/nicedreamzapp/browser-broker/issues/1): Ubuntu 26.04 / Chrome 153, two concurrent clients, checked in both directions. Then measured that hidden tabs never fire `requestAnimationFrame` while their timers keep running, and that `Page.captureScreenshot` still returns a PNG. That corrected a claim the project had been making in three files. He maintains [Hronaut](https://github.com/Hronom); this was a standalone test, not an integration. |

## Sibling projects

| Project | Role |
|---|---|
| [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) | Claude Code on a local MLX model |
| [browser-agent](https://github.com/nicedreamzapp/browser-agent) | Drives your real browser via CDP; the agent browser-broker was first built to keep out of the human's tabs |

## Prior art and neighbours

Other projects working on the same problem, listed in the README:

- [vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser): `--cdp` mode with `--pin-tab`.
- [leeguooooo/chrome-use](https://github.com/leeguooooo/chrome-use): per-session tab groups in your real Chrome.
- [mediar-ai/playwright-mcp-orchestrator](https://github.com/mediar-ai/playwright-mcp-orchestrator): one Chrome, many `@playwright/mcp` sessions.
- [henu-wang/chrome-mcp-proxy](https://github.com/henu-wang/chrome-mcp-proxy) and `chrome-devtools-mcp-mux`: proxies in front of `chrome-devtools-mcp`.
- [captivus/chrome-agent](https://github.com/captivus/chrome-agent), [pasky/chrome-cdp-skill](https://github.com/pasky/chrome-cdp-skill), [ofershap/real-browser-mcp](https://github.com/ofershap/real-browser-mcp): drive your real, logged-in browser.

If your work is here and you'd like the wording changed, or something is missing,
[open an issue](https://github.com/nicedreamzapp/browser-broker/issues).
