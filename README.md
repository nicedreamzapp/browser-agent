# Local Browser Agent

[![GitHub stars](https://img.shields.io/github/stars/nicedreamzapp/browser-agent?style=for-the-badge&logo=github&color=f5c542&labelColor=1f2328)](https://github.com/nicedreamzapp/browser-agent/stargazers)

Autonomous browser agent running entirely on Apple Silicon. No cloud APIs, no Claude Code overhead, no MCP layer. Direct MLX inference + Chrome DevTools Protocol.

## Architecture

```
User prompt → Local LLM (MLX) → Chrome DevTools Protocol → Brave Browser
                   ↑                       ↓
             ~2–5s per step        DOM.pierce + DOM.focus
                                   + Input.insertText
```

**Default model**: Gemma 4 31B Instruct abliterated (4-bit quantized) via MLX on Apple Silicon
**Alternative models**: any MLX-compatible model — Qwen 3.5 122B (biggest), Llama 3.3 70B (smartest), or anything else — swap via the `MLX_MODEL` env var
**Browser**: Brave with remote debugging on port 9222
**Protocol**: CDP WebSocket — no MCP, no proxy, direct connection

## Key Innovation: Cross-Origin Iframe + Shadow DOM Commenting

Most news sites (Yahoo, etc.) use third-party comment widgets (OpenWeb/SpotIM) that load inside:
1. A **cross-origin iframe** (JavaScript can't access it)
2. A **Shadow DOM** (normal querySelector can't find elements)
3. A **ProseMirror rich text editor** (innerHTML doesn't work)

Standard browser automation tools (Playwright, Selenium, MCP) fail at all three layers.

**Our solution uses CDP primitives that bypass all of these:**

```
DOM.getDocument(depth: -1, pierce: true)    # Exposes everything across iframes + Shadow DOM
DOM.performSearch(".ProseMirror")            # Finds the editor in any context
DOM.focus(nodeId)                            # Focuses it regardless of origin
Input.insertText(text)                       # Types into the focused element
```

This works because CDP operates at the **browser level**, not the page level. Same-origin policy doesn't apply.

## Setup

### Prerequisites
- macOS with Apple Silicon (M-series), 32 GB+ unified memory recommended
- Brave Browser (or Chrome) with remote debugging
- Python 3.12+ with MLX

### Install

```bash
# MLX server backend (handles local inference)
pip install mlx mlx-lm websockets
```

### MLX Server

The agent talks to a local MLX inference server that speaks Anthropic's Messages API.
The server ships with the companion repo [claude-code-local](https://github.com/nicedreamzapp/claude-code-local) — set that up first. Once installed, the server lives at `~/.local/mlx-native-server/server.py` and is auto-started by the desktop launcher.

### Launcher

Desktop launcher: double-click `Gemma 4 Browser.command` (from the claude-code-local repo's `launchers/Browser Agent.command`). The launcher will:

1. Start the MLX server with Gemma 4 31B if it isn't already running
2. Start Brave with `--remote-debugging-port=9222` if it isn't already running
3. Ensure at least one page tab exists
4. Hand off to the Python agent

## Usage

### Interactive Mode (recommended)
```bash
python agent.py
# Prompts: "What should I do?"
# Type tasks, get results, stays open for the next task
# Type "quit" to exit
# Errors in one task no longer kill the whole session — you'll just get a
# message and a fresh prompt
```

### One-Shot Mode
```bash
python agent.py "Find an article about Iran on Yahoo and make a comment"
```

### Swap Models
```bash
# Override the default model with any MLX-compatible LLM
MLX_MODEL="mlx-community/Qwen2.5-72B-Instruct-4bit" python agent.py
```

## Example Tasks

### Comment on a news article
```
Find an article about Iran on Yahoo and make a comment. Don't post it, just leave it in draft.
```

The agent will:
1. Navigate to Yahoo News
2. Find an Iran article via JavaScript (instant, no model needed)
3. Click the article
4. Read the article content (first 6 paragraphs)
5. Generate a relevant 2–3 sentence comment using the model
6. Open the Comments section
7. Find the comment widget (cross-origin iframe + Shadow DOM)
8. Type the comment via DOM.pierce + DOM.focus + Input.insertText
9. Scroll so you can see the comment
10. NOT click Send — leaves it for your review

### With specific comment text
```
Go to Yahoo, find an Iran article. comment: The diplomatic situation demands more transparency from all parties involved.
```

## How It Works

### Fast Path (comment tasks)
When the task mentions "comment" plus a topic keyword (iran, trump, etc.):
1. **JavaScript finds the article** — no model needed, instant
2. **Model generates the comment** — reads article paragraphs, writes 2–3 sentences
3. **CDP types the comment** — pierces through iframes and Shadow DOM

### General Path (other tasks)
The model controls the browser via JSON tool calls:
- `navigate(url)` — go to a page
- `snapshot()` — get accessibility tree with element UIDs
- `click(uid)` — click an element
- `type_text(uid, text)` — type into an element
- `scroll(direction)` — scroll up/down
- `js(code)` — run arbitrary JavaScript
- `done(message)` — task complete

Built-in loop detection: if the same UID gets clicked more than twice in a row, the agent presses Escape (to dismiss any lightbox/overlay) and forces a fresh snapshot so the model can try a different approach.

### Error Recovery
Any exception during a task (MLX timeout, CDP websocket drop, malformed model output, etc.) is caught by the main loop — you'll see the error printed and return to the prompt rather than the whole agent crashing.

## Performance (Gemma 4 31B on M-series, warm disk cache)

| Metric | Value |
|--------|-------|
| Navigate + snapshot | ~4s |
| Article finding (JS) | <1s |
| Comment generation | ~8s |
| Comment typing (pierce + type) | ~3s |
| **Total for comment task** | **~20–30s** |

## Files

- `agent.py` — The browser agent (single file, ~470 lines)
- `~/.local/mlx-native-server/server.py` — MLX inference server with Anthropic API + tool parsing (ships with [claude-code-local](https://github.com/nicedreamzapp/claude-code-local))
- `launchers/Browser Agent.command` — Desktop launcher (ships with [claude-code-local](https://github.com/nicedreamzapp/claude-code-local), surfaces as `Gemma 4 Browser.command` on the Desktop)

## Built With

- [MLX](https://github.com/ml-explore/mlx) — Apple's ML framework for Apple Silicon
- [Gemma 4 31B](https://huggingface.co/google/gemma-4-31b-it) — instruction-tuned, abliterated and 4-bit quantized
- Chrome DevTools Protocol — direct browser control via WebSocket
- No cloud APIs, no subscriptions, no data leaving your machine
