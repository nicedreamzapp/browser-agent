# Local Browser Agent

Autonomous browser agent running entirely on Apple Silicon. No cloud APIs, no Claude Code overhead, no MCP layer. Direct MLX inference + Chrome DevTools Protocol.

## Architecture

```
User prompt → Qwen 3.5 122B (MLX) → Chrome DevTools Protocol → Brave Browser
                    ↑                          ↓
              2-5s per step          DOM.pierce + DOM.focus
                                     + Input.insertText
```

**Model**: Qwen 3.5 122B-A10B (4-bit quantized) via MLX on Apple Silicon
**Browser**: Brave with remote debugging (port 9222)
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
DOM.focus(nodeId)                           # Focuses it regardless of origin
Input.insertText(text)                      # Types into the focused element
```

This works because CDP operates at the **browser level**, not the page level. Same-origin policy doesn't apply.

## Setup

### Prerequisites
- macOS with Apple Silicon (M-series)
- Brave Browser (or Chrome) with remote debugging
- Python 3.12+ with MLX

### Install

```bash
# MLX server (handles Qwen 3.5 inference)
pip install mlx mlx-lm

# Agent dependency
pip install websockets

# Start Brave with remote debugging
open -a "Brave Browser" --args --remote-debugging-port=9222
```

### MLX Server

The agent talks to an MLX inference server that speaks Anthropic's Messages API:

```bash
# Start the server (loads Qwen 3.5 122B)
python ~/.local/mlx-native-server/server.py
# Serves on http://localhost:4000
```

## Usage

### Interactive Mode (recommended)
```bash
python agent.py
# Prompts: "What should I do?"
# Type tasks, get results, stays open for next task
# Type "quit" to exit
```

### One-Shot Mode
```bash
python agent.py "Find an article about Iran on Yahoo and make a comment"
```

### Desktop Launcher
Double-click `Browser Agent.command` on Desktop. Starts MLX server + Brave if needed.

## Example Tasks

### Comment on a news article
```
Find an article about Iran on Yahoo and make a comment. Don't post it, just leave in draft.
```
The agent will:
1. Navigate to Yahoo News
2. Find an Iran article via JavaScript (instant, no model needed)
3. Click the article
4. Read the article content (first 6 paragraphs)
5. Generate a relevant 2-3 sentence comment using the model
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
When the task mentions "comment" + a topic keyword (iran, trump, etc.):
1. **JavaScript finds the article** — no model needed, instant
2. **Model generates comment** — reads article paragraphs, writes 2-3 sentences
3. **CDP types the comment** — pierce through iframes + Shadow DOM

### General Path (other tasks)
The model controls the browser via JSON tool calls:
- `navigate(url)` — go to a page
- `snapshot()` — get accessibility tree with element UIDs
- `click(uid)` — click an element
- `type_text(uid, text)` — type into an element
- `scroll(direction)` — scroll up/down
- `js(code)` — run JavaScript
- `done(message)` — task complete

### Comment Text Extraction
Qwen 3.5 outputs verbose reasoning (drafts, critiques, analysis) as plain text. The agent filters this to extract only the clean comment:
- Strips `<think>` tags
- Removes markdown formatting
- Filters sentences containing meta-words (draft, constraint, analyze, etc.)
- Takes the last 2-3 real sentences (model's final/best output)

## Performance

| Metric | Value |
|--------|-------|
| Navigate + snapshot | ~4s |
| Article finding (JS) | <1s |
| Comment generation | ~8s |
| Comment typing (pierce + type) | ~3s |
| **Total for comment task** | **~20-30s** |

## Files

- `agent.py` — The browser agent (single file, ~300 lines)
- `~/.local/mlx-native-server/server.py` — MLX inference server with Anthropic API + tool parsing
- `~/Desktop/Browser Agent.command` — Desktop launcher

## Built With

- [MLX](https://github.com/ml-explore/mlx) — Apple's ML framework for Apple Silicon
- [Qwen 3.5 122B](https://huggingface.co/mlx-community/Qwen3.5-122B-A10B-4bit) — 4-bit quantized LLM
- Chrome DevTools Protocol — direct browser control via WebSocket
- No cloud APIs, no subscriptions, no data leaving your machine
