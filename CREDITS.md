# 🙏 Credits

None of this starts from scratch. Here's whose work this is built on.

| Project | What it does here | By |
|---|---|---|
| 🍎 [MLX](https://github.com/ml-explore/mlx) | Apple Silicon ML framework | Apple's ml-explore team |
| 🟢 [Gemma](https://blog.google/technology/developers/gemma-open-models/) | The default model driving the agent | Google DeepMind |
| 🌐 [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) | Everything this agent does to a browser | The Chromium team |
| 🤖 [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) | The MLX inference server it talks to | sibling project |
| 🔌 [websockets](https://github.com/python-websockets/websockets) | The agent's CDP socket, and the broker's proxy | Aymeric Augustin and contributors |
| 🔌 [websocket-client](https://github.com/websocket-client/websocket-client) | The broker's lease client | Hiroki Ohtani and contributors |
| 👁️ [Agent-Reach](https://github.com/Panniantong/Agent-Reach) | The "first choice, then fallback" order for reading a site (SITE-RECIPES.md) | Panniantong |

## Field reports

Testing on hardware I don't own, from people who had no reason to bother.

| Who | What they found |
|---|---|
| 🐧 [@Hronom](https://github.com/Hronom) | Verified the broker's isolation on Linux ([browser-broker #1](https://github.com/nicedreamzapp/browser-broker/issues/1)): Ubuntu 26.04 / Chrome 153, two concurrent clients, checked in *both* directions. Then found that hidden tabs never paint: 0 animation frames while the timers kept ticking, and `Page.captureScreenshot` still handing back a valid PNG off a page that had drawn nothing. That corrected a claim the project had been making in three files. He maintains [Hronaut](https://github.com/Hronom); this was a standalone test, not an integration. |

The broker's own credits are in [broker/CREDITS.md](broker/CREDITS.md).

---

If your work is here and you'd like the wording changed, or if something's
missing, open an issue and I'll fix it.

[← back to the README](README.md)
