# 🙏 Credits

None of this starts from scratch. Here's whose work this is built on.

| Project | What it does here | By |
|---|---|---|
| 🌐 [Chrome DevTools Protocol](https://chromedevtools.github.io/devtools-protocol/) | Every tab the broker opens, parks, and reclaims | The Chromium team |
| 🔌 [websocket-client](https://github.com/websocket-client/websocket-client) | The one dependency — the CDP socket | Hiroki Ohtani and contributors |
| 🤖 [browser-agent](https://github.com/nicedreamzapp/browser-agent) | The agent this was built to keep out of the human's tabs | sibling project |
| 🧠 [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) | The local MLX brain those agents think with | sibling project |

## Field reports

Testing on hardware I don't own, from people who had no reason to bother.

| Who | What they found |
|---|---|
| 🐧 [@Hronom](https://github.com/Hronom) | Verified the isolation guarantee on Linux ([#1](https://github.com/nicedreamzapp/browser-broker/issues/1)) — Ubuntu 26.04 / Chrome 153, two concurrent clients, and he checked it in *both* directions instead of just confirming his own tabs showed up. Then ran the follow-up nobody asked twice for and found that hidden tabs never paint: 0 animation frames while the timers kept ticking, and `Page.captureScreenshot` still handing back a valid PNG off a page that had drawn nothing. That one corrected a claim this project had been making in three files. He maintains [Hronaut](https://github.com/Hronom); this was a standalone test, not an integration. |

---

If your work is here and you'd like the wording changed, or if something's
missing, open an issue and I'll fix it.

[← back to the README](README.md)
