# Local Agent

<p align="center">
  <a href="https://youtu.be/SIuMNa2k6Wc">
    <img src="https://img.youtube.com/vi/SIuMNa2k6Wc/maxresdefault.jpg" width="640" alt="Local AI browser agent demo">
  </a>
  <br>
  <em>▶ Watch the demo on YouTube</em>
</p>


[![GitHub stars](https://img.shields.io/github/stars/nicedreamzapp/browser-agent?style=for-the-badge&logo=github&color=f5c542&labelColor=1f2328)](https://github.com/nicedreamzapp/browser-agent/stargazers)
[![Join the NiceDreamzApps Discord](https://img.shields.io/discord/1497121921580404818?label=NiceDreamzApps&logo=discord&color=5865F2&style=for-the-badge)](https://discord.gg/ZdSqgAxUW)

<a id="s1"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s01-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s01-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s01-banner.svg" width="100%" alt="01. What it does. Plain English in, a real browser and shell out"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s01-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s01-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s01-r1.svg" width="100%" alt="Plain English in. Type a task and a model on your own Mac picks the steps. Your real Brave. It drives your logged-in browser and your shell. No cloud APIs. Direct MLX inference plus Chrome DevTools Protocol."></picture>

**What it does:** you type a task in plain English, a model running on your own Mac picks the steps, and the agent carries them out in your real Brave browser and your shell. The demo above shows it working end to end.

An autonomous agent that runs entirely on Apple Silicon — it drives your real browser **and** your Mac. No cloud APIs, no Claude Code overhead, no MCP layer. Direct MLX inference + Chrome DevTools Protocol, plus a shell/file/media toolbelt for everything that isn't the web.

> Started life as a pure browser agent. It has since grown a full system toolbelt (shell, files, screenshots, screen recording, send-to-phone), so it now handles end-to-end tasks like "find X on the web, run a script, and text me the result" in one session — all locally.

> **Running more than one agent against the same browser?** Use [browser-broker](https://github.com/nicedreamzapp/browser-broker) — it leases each agent its own background tab, so agents never fight over tabs or touch the one you're reading.

> **Helping it along?** Type in its form or solve its captcha yourself: it notices, waits for you, and continues from what you did instead of starting over. See [Working with the human](#working-with-the-human-five-rules).

## What I built

Matt Macosko wrote the agent itself, all of it in [`agent.py`](agent.py):

- **The agent loop**: model call, JSON tool-call parsing (single call or a batch of up to 5), step limit, loop detection ([`run`](agent.py#L1123), [`parse_multi`](agent.py#L1096))
- **A raw CDP client** with no Playwright or Selenium: connect, reconnect to the same tab, accessibility-tree snapshots, click, one-call typing, scroll, JS ([`class CDP`](agent.py#L718))
- **The iframe + Shadow DOM comment drafter** that pierces cross-origin widgets like OpenWeb ([`post_comment`](agent.py#L911))
- **A prompt-injection guard** in code that the model cannot switch off ([`guard_check`](agent.py#L465), notes at [line 332](agent.py#L332))
- **The system toolbelt**: shell, file read/write, screenshots, send-to-phone, Studio Record control ([lines 29 to 247](agent.py#L29))
- **browser-broker tab leasing** so several agents can share one browser ([`_broker_tab`](agent.py#L541))
- **Working with the human**: look before every step, notice and adopt what you typed or clicked, never reload or reopen mid-job, and stop and ask when stuck ([the five rules](agent.py#L587))

Upstream, not built here: [MLX](https://github.com/ml-explore/mlx) (Apple), the Gemma 4 model (Google DeepMind), the Chrome DevTools Protocol (Chromium), and the MLX server from the sibling [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) repo. Full list in [CREDITS.md](CREDITS.md).

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s01-rule.svg" width="100%" alt="">

<a id="s2"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s02-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s02-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s02-banner.svg" width="100%" alt="02. How it works. Architecture, the sibling projects, and the iframe trick"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s02-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s02-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s02-r1.svg" width="100%" alt="Default model. Gemma 4 31B, 4-bit, via MLX on Apple Silicon. Browser. Brave with remote debugging on port 9222. Protocol. CDP WebSocket, no MCP, no proxy."></picture>

```
User prompt → Local LLM (MLX) → ┬─ Chrome DevTools Protocol → Brave Browser
                   ↑             ├─ shell / read_file / write_file → macOS
             ~2–5s per step      ├─ screenshot / fullscreen_shot → send to phone
                                 └─ Studio Record → screen video → send to phone
```

**Default model**: Gemma 4 31B Instruct abliterated (4-bit quantized) via MLX on Apple Silicon
**Alternative models**: any MLX-compatible model, chosen when you start the MLX server (see [Swap Models](#swap-models)). In our Sep 16 test (four jobs run one at a time, then the same four jobs in four tabs at once) Gemma 4 31B and Qwen 3.8 27B each got all 8 runs right, 4/4 in each mode; Gemma took 123 s one at a time and 110 s four at once, Qwen took 175 s and 176 s
**Browser**: Brave with remote debugging on port 9222
**Protocol**: CDP WebSocket — no MCP, no proxy, direct connection

## Part of a local-first ecosystem

This agent shares its brain and plumbing with a few sibling projects, all running on-device:

- **[claude-code-local](https://github.com/nicedreamzapp/claude-code-local)** — the MLX inference server (Anthropic Messages API + tool parsing) this agent talks to, plus the desktop launchers. Set it up first.
- **NarrateClaude** — the same local stack wired for voice narration; this agent's "send to phone" and media tooling come from the same family.

You don't need the whole ecosystem to run the agent — just the MLX server from claude-code-local — but it's built to compose with them.

## Key Innovation: Cross-Origin Iframe + Shadow DOM control

Most news sites (Yahoo, etc.) load interactive widgets (e.g. OpenWeb/SpotIM comments) inside:
1. A **cross-origin iframe** (JavaScript can't access it)
2. A **Shadow DOM** (normal querySelector can't find elements)
3. A **ProseMirror rich text editor** (innerHTML doesn't work)

Standard browser automation tools (Playwright, Selenium, MCP) fail at all three layers.

**This agent uses CDP primitives that bypass all of them:**

```
DOM.getDocument(depth: -1, pierce: true)    # Exposes everything across iframes + Shadow DOM
DOM.performSearch(".ProseMirror")            # Finds the editor in any context
DOM.focus(nodeId)                            # Focuses it regardless of origin
Input.insertText(text)                       # Types into the focused element
```

This works because CDP operates at the **browser level**, not the page level. Same-origin policy doesn't apply. Because it drives your **real, logged-in Brave** over CDP (not a fresh Playwright profile), authenticated sites just work.

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s02-rule.svg" width="100%" alt="">

<a id="s3"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s03-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s03-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s03-banner.svg" width="100%" alt="03. Tools. What the model can call"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s03-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s03-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s03-r1.svg" width="100%" alt="Web. navigate, snapshot, click, type_text, scroll, js. System. shell, read_file, write_file. Media. Screenshots, recording, send to phone."></picture>

The model controls everything through JSON tool calls — one per turn, **or a batch**: it can return a JSON array of up to 5 calls that execute back-to-back on a single model turn (fill three fields, click submit — one think instead of four). After `navigate`/`click`/`type_text`/`scroll`, the fresh page state is attached to the result automatically (once per batch, after the last action), so it rarely needs a separate `snapshot`.

### Web
- `navigate(url)` — go to a page
- `snapshot()` — get the page's elements with UIDs (rarely needed; auto-attached after actions)
- `click(uid)` — click an element by UID
- `type_text(uid, text)` — type into a field by UID (one-shot `Input.insertText` — instant, even for long text)
- `scroll(direction)` — `"up"` / `"down"`
- `js(code)` — run arbitrary JavaScript and return a value

### System
- `shell(cmd, timeout?)` — run any bash command (default cwd = `$HOME`). First choice for git, ssh, curl, wp-cli, python, npm, file ops — anything the terminal handles in one line
- `read_file(path)` — read a file or list a directory (`~` expands)
- `write_file(path, content)` — overwrite a file, creating parent dirs

### Media / send-to-phone
- `screenshot()` — capture the current Brave tab and text it
- `fullscreen_shot()` — capture the whole Mac desktop (all displays) and text it
- `send_image(url)` / `send_video(url)` — download a URL and text it
- `record_start(mode)` — start Studio Record (`screen` / `face` / `screen_face`)
- `record_stop()` — stop recording and auto-text the `.mp4`

### Control
- `done(message)` — task complete (also used for conversational answers)

The code also handles a `comment(text)` action (the iframe + Shadow DOM drafter above), but it is not listed in the model's tool prompt, so the model only uses it if it names it on its own.

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s03-rule.svg" width="100%" alt="">

<a id="s4"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s04-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s04-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s04-banner.svg" width="100%" alt="04. Setup and usage. Install, launch, settings, example tasks"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s04-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s04-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s04-r1.svg" width="100%" alt="Prerequisites. macOS on Apple Silicon, 32 GB+ recommended. Install. websockets, plus mlx and mlx-lm for the server. MLX server. Comes from the claude-code-local repo."></picture>

### Prerequisites
- macOS with Apple Silicon (M-series), 32 GB+ unified memory recommended
- Brave Browser (or Chrome) with remote debugging
- Python 3.12+ with MLX

### Install

```bash
pip install websockets        # the only third-party package agent.py imports
pip install mlx mlx-lm        # for the MLX server from claude-code-local
```

### MLX Server

The agent talks to a local MLX inference server that speaks Anthropic's Messages API. It ships with the companion repo **[claude-code-local](https://github.com/nicedreamzapp/claude-code-local)** — set that up first. Once installed, the server lives at `~/.local/mlx-native-server/server.py` and is auto-started by the desktop launcher.

### Launcher

Double-click `Gemma 4 Browser.command` (from the claude-code-local repo's `launchers/Browser Agent.command`). It will:

1. Start the MLX server with Gemma 4 31B if it isn't already running
2. Start Brave with `--remote-debugging-port=9222` if it isn't already running
3. Ensure at least one page tab exists
4. Hand off to the Python agent

The media tools (`screenshot`, `record_*`, `send_*`) shell out to local helper scripts (`~/.claude/imessage-*.sh`, Studio Record on `127.0.0.1:17494`). They only send when the flag file `~/.claude/imessage-agent-on` exists; otherwise they return "mobile mode is off". They degrade gracefully — if a helper isn't present, that tool just reports it's unavailable; the rest of the agent runs fine.

### Settings (env vars read by `agent.py`)

| Variable | Default | What it sets |
|---|---|---|
| `MLX_URL` | `http://localhost:4000` | MLX server (Anthropic Messages API) |
| `MLX_MODEL_NAME` | `claude-sonnet-4-6` | model name sent in each request |
| `CDP_URL` | `http://127.0.0.1:9222` | browser debugging endpoint |
| `BROKER_URL` | `http://127.0.0.1:9223` | browser-broker API (optional; falls back to raw CDP) |
| `BROWSER_AGENT_VISIBLE` | unset | set to `1` to ask browser-broker for a visible tab |
| `MAX_STEPS` | `30` | model turns per task before it gives up |

## Usage

### Interactive Mode (recommended)
```bash
python agent.py
# Prompts: "What should I do?"
# Type tasks, get results, stays open for the next task
# Type "quit" to exit
# Errors in one task no longer kill the session — you get a message and a fresh prompt
```

### One-Shot Mode
```bash
python agent.py "Find an article about Iran on Yahoo and draft a comment"
python agent.py "cd into my site repo, run the build, and text me a screenshot when it's done"
```

### Swap Models
`agent.py` does not load a model. It sends requests to the MLX server at `MLX_URL`, and the server decides which model answers. Choose the model when you start the server from claude-code-local, for example:
```bash
MLX_MODEL="lmstudio-community/Qwen3.8-27B-MLX-8bit"   # set in the server's environment, not the agent's
```

## Example Tasks

**Web + system in one go**
```
Find the newest release on the MLX GitHub, save the changelog to ~/Desktop/mlx-notes.txt, and text me a screenshot of the release page.
```

**Comment on a news article** (the original use case — leaves it in draft for review)
```
Find an article about Iran on Yahoo and make a comment. Don't post it, just leave it in draft.
```
The agent navigates, finds the article, and reads it. When it drafts through the `comment` action, the code pierces the cross-origin iframe + Shadow DOM to type the text, scrolls so you can see it, and does **not** click Send.

**Pure terminal task** (no browser opened)
```
Show me which of my LaunchAgents failed to load and tail the last 20 lines of each one's log.
```

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s04-rule.svg" width="100%" alt="">

<a id="s5"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s05-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s05-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s05-banner.svg" width="100%" alt="05. Performance and safety. Where the time goes, and what pages cannot make it do"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s05-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s05-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s05-r1.svg" width="100%" alt="117x faster typing on a 300-char field. ~1 ms to type text with Input.insertText. ~20-30 s total for a comment task."></picture>

## How It Works

### Reliability
- **Batched actions, guarded** — a batch executes in order, stops at the first error (skipping the rest so a broken plan can't keep firing), and reports every action's result numbered so the model knows exactly how far it got.
- **Auto-attached page state** — the fresh DOM is returned with each action result (once per batch), so the model doesn't waste turns re-snapshotting.
- **Loop detection** — if the same UID is clicked a third time, the agent stops and asks you one question instead of trying again (see the five rules below).
- **Error recovery** — any exception during a task (MLX timeout, CDP websocket drop, malformed output) is caught by the main loop; you get the error and a fresh prompt instead of a crash.

### Close what you open
**The problem.** Agents kept leaving tabs open after they finished their jobs. The tabs piled up in Matt's browser across all three machines (the M5, the mini and the Windows PC), and he had to keep asking for them to be cleared. This agent used to hold its tab for the whole interactive session, so a finished task's page sat there until you quit.

**What it does now.** Each task gets its own tab, and [`run`](agent.py#L1123) closes it in a `finally` the moment the task ends: done, out of steps, failed, or interrupted. Reconnects inside a task go back to the same tab instead of opening another. On exit, `atexit` plus SIGTERM/SIGHUP handlers close anything still open, and if the process is killed outright the browser-broker lease expires and the broker closes the tab. Tabs you opened yourself are never touched.

**The rule for any agent using this:** close every tab you opened when the task is done.

### Working with the human: five rules
**The problem.** On the night of October 22, 2026, agents fought the human over the same browser tab. An agent opened an opt-out form, started filling it in, closed the tab, opened it again and started over, more than ten times, right in front of him. When he stepped in to help, typing in the form or solving the captcha himself, the agent never noticed. It reloaded the page and wiped his work, or opened the same link on top of the page he had just submitted. The agent never looked at what the page showed right now, so it could not know what he had already done.

**What it does now.** Five rules, each one in code ([the rules block](agent.py#L587)), working together with [browser-broker](https://github.com/nicedreamzapp/browser-broker), which enforces the same rules from its side:

| Rule | How this agent keeps it |
|---|---|
| **1. One tab per job.** | One broker lease per task: the same tab for the whole job, reconnects included. When a task stops to ask you something, the tab is kept, and your answer continues the job in that same tab instead of starting a new one. |
| **2. Look before every step.** | [`look()`](agent.py#L745) reads the tab (URL, title, every filled-in field, passwords masked) before each model turn and again right before each click or keystroke. |
| **3. Notice when the human helps.** | A listener records real keyboard and mouse input (`event.isTrusted`; the agent's own input is recognized and not counted). While you are typing or clicking in its tab, the agent pauses. When you stop, it tells the model exactly what you changed ("Email now holds ...", "the page moved to /success") and continues from there. It never goes back to a page you already moved past. |
| **4. No reloading or reopening mid-job.** | Navigating to the page it is already on is refused in code (that is a reload, and a reload wipes your work). The broker refuses reloads, closes and input in a tab you are working in, and the agent waits instead of treating that as an error. |
| **5. Stuck means ask, not retry.** | One failure gets one clearly different try. A second failure in a row, clicking the same thing three times, reopening a page after a failure, or running out of steps stops the task with one short question (also texted to your phone in mobile mode). The model can call `ask(question)` itself. |

What you see:

```text
  Step 3 click(uid='41')
  The human is working in the tab: pausing until he stops.
  The human helped: "Captcha" now holds "solved"; the human typed or clicked in this tab 3s ago
  Step 4 click(uid='57')
  ...
NEED YOU: I'm stuck on https://people.example/optout: Error: can't resolve. What should I do?

Your answer? use the second email
```

Every rule has its own tests in [`tests/test_five_rules.py`](tests/test_five_rules.py) (a fake tab and a scripted model, no browser needed; `python3 -m pytest tests`), and [`tests/live_hidden_tab_check.py`](tests/live_hidden_tab_check.py) checks the listener against a real browser in a hidden tab.

### Safety: web pages can't give orders
Page text is treated as data. These checks live in code ([`guard_check`](agent.py#L465)), so a page can't talk the model out of them:
- Text a human can't see (tiny, transparent, off-screen, same colour as the background) and hidden buttons or fields are dropped from the snapshot.
- Visible text that addresses an AI and gives an order is labelled untrusted.
- After a page has been read, shell, file and phone tools only run if your task asked for that kind of work, and commands or files built from page text are refused.
- Risky clicks (delete, buy, pay, send, post, sign in...) and password or card fields need the task to ask for them. Banking and payment sites must be named in the task. Text read on one site can't be sent to another.
- `js()` can read the page but not send requests or change the page's location.

## Performance

Two things are true at once: every individual browser action is near-instant, and a full task still takes tens of seconds — because the model reasons for 2–5s *between* each action. We profiled the whole pipeline against real pages (M-series Mac, isolated tab, warm cache) to find out exactly where the time goes.

**Per-operation — the browser mechanics (all sub-second):**

| Operation | Time | Notes |
|-----------|------|-------|
| Type text (`Input.insertText`) | **~1 ms** | was ~108 ms char-by-char — **117× faster** on a 300-char field |
| Page snapshot (a11y tree) | 2 ms → ~330 ms | near-instant on light pages, heavier on content-dense ones (Wikipedia) |
| Navigate + wait-for-ready | 150–370 ms | polls `readyState` instead of sleeping a fixed worst-case |
| Scroll | ~150 ms | |
| `js()` eval | <1 ms | |

**Per-step — where the time actually goes:**

| Metric | Value |
|--------|-------|
| **Model reasoning per step** | **2,000–5,000 ms** (≈99% of a step) |
| Comment generation | ~8 s |
| **Total for a comment task** | **~20–30 s** |

**The honest takeaway from profiling:** the browser was never the bottleneck — the local model is. Typing, clicking, scrolling, and reading the page are all sub-second (typing is now effectively free). Total task time ≈ per-step model latency × number of steps, so the real speed levers are a faster local model or fewer steps per task — not faster browser plumbing.

**That's exactly what batch mode attacks.** The model can return an array of up to 5 tool calls that execute in sequence on one turn. A fill-two-fields-and-submit sequence that used to cost three model thinks (~6–15 s) now costs one (~2–5 s). The safety rules keep it honest: element uids only exist for a page the model has already seen, so it's told never to batch a click/type against a page it hasn't loaded yet; an error mid-batch stops the remaining actions immediately and hands control back to the model with the numbered results; and the auto-snapshot runs once after the batch's last action instead of after every action.

### What got faster (and why)

- **Typing is one call, not hundreds.** `type_text` used to dispatch a `keyDown`+`keyUp` pair for *every character* — 400 WebSocket round-trips to type a 200-char comment. It now sends the whole string in a single `Input.insertText`, with a one-key arrow nudge to wake up search-as-you-type/React listeners, and a per-character fallback for the rare field that rejects bulk insert. Works the same in plain inputs, textareas, and contenteditable/rich editors. Measured 117× faster.
- **No more blind sleeps.** The fixed waits after `navigate`/`click`/`scroll` were trimmed hard (navigate settle 300→150 ms, scroll 500→150 ms, click 200→80 ms). Navigation still *polls* `readyState` for correctness — it just stops waiting the instant the page is genuinely ready instead of always sleeping the worst case.

## Known limits

- macOS only in practice: the media tools, launcher and screen capture assume a Mac, and the agent needs the separate MLX server running.
- One model turn takes 2 to 5 seconds, so a full task takes tens of seconds (see Performance).
- A task stops after `MAX_STEPS` (30) model turns.
- The `comment` drafter is tuned for OpenWeb/SpotIM-style widgets and falls back to the first text box on the page.
- Phone features depend on your own helper scripts and Studio Record; without them those tools report they're off.

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s05-rule.svg" width="100%" alt="">

<a id="s6"></a>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s06-banner-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s06-banner-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s06-banner.svg" width="100%" alt="06. Credits and community. Files, what it is built with, alternatives, help"></picture>
<picture><source media="(max-width: 610px), (min-width: 768px) and (max-width: 895px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s06-r1-m.svg"><source media="(max-width: 1149px)" srcset="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s06-r1-mid.svg"><img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s06-r1.svg" width="100%" alt="Built with. MLX, Gemma 4 31B and the Chrome DevTools Protocol. Credits. Whose work this builds on is in CREDITS.md. Community. The NiceDreamzApps Discord."></picture>

## Files

- `agent.py` — the agent (single file)
- `tests/` — the five-rules tests (`python3 -m pytest tests`) and a live hidden-tab check
- `~/.local/mlx-native-server/server.py` — MLX inference server (ships with [claude-code-local](https://github.com/nicedreamzapp/claude-code-local))
- `launchers/Browser Agent.command` — desktop launcher (ships with claude-code-local, surfaces as `Gemma 4 Browser.command`)

## Built With

- [MLX](https://github.com/ml-explore/mlx) — Apple's ML framework for Apple Silicon
- [Gemma 4 31B](https://huggingface.co/google/gemma-4-31b-it) — instruction-tuned, abliterated and 4-bit quantized
- Chrome DevTools Protocol — direct browser control via WebSocket
- No cloud APIs, no subscriptions, no data leaving your machine

## Other ways to do this

Plenty of good people are working on agents that drive a browser. If this one doesn't fit, one of these might:

- **[browser-use](https://github.com/browser-use/browser-use)** is a Python library for building browser agents on top of whichever model you like. The most complete option if you want to build your own agent.
- **[chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp)**, from the Chrome DevTools team, gives coding agents like Claude Code direct control of Chrome over MCP, including performance traces.
- **[Playwright MCP](https://github.com/microsoft/playwright-mcp)** does the same through Playwright and the page's accessibility tree, so the model works from structure instead of screenshots.
- **[agent-browser](https://github.com/vercel-labs/agent-browser)** is a browser automation CLI built for AI agents to call.

This one is small on purpose: one file, a local MLX model, and raw CDP, so nothing leaves your Mac.

## Something not working?

Open an [issue](https://github.com/nicedreamzapp/browser-agent/issues/new) with the task you gave it, the site, and the last lines of output. Whose work this is built on is in [CREDITS.md](CREDITS.md).

## 💬 Community

Builders running this stack hang out in the NiceDreamzApps Discord — quiet, builder-tone, no bots. Share what you're scraping, what's breaking, what local model worked for which site.

👉 **[discord.gg/ZdSqgAxUW](https://discord.gg/ZdSqgAxUW)**

<img src="https://raw.githubusercontent.com/nicedreamzapp/browser-agent/312339baec47b6f4a1e5a94c4f85e812744c5c6c/assets/readme/s06-rule.svg" width="100%" alt="">
