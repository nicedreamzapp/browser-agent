#!/usr/bin/env python3
"""
Local Browser Agent — Direct MLX + Chrome DevTools Protocol.
Handles iframes, Shadow DOM, ProseMirror editors automatically.
"""

import json, os, re, sys, time, asyncio, subprocess, websockets, urllib.request

_MOBILE_FLAG = os.path.expanduser("~/.claude/imessage-agent-on")
_IMSG_SEND = os.path.expanduser("~/.claude/imessage-send.sh")

_IMSG_SEND_IMG = os.path.expanduser("~/.claude/imessage-send-image.sh")
_IMSG_SEND_VID = os.path.expanduser("~/.claude/imessage-send-video.sh")
_DL_DIR = os.path.expanduser("~/Downloads/gemma-agent")

# Hosts whose image URLs are almost always tiny thumbnails or data-URI stubs —
# sending these results in fuzzy, useless previews on the phone.
_BAD_IMAGE_HOSTS = (
    "encrypted-tbn0.gstatic.com",
    "encrypted-tbn1.gstatic.com",
    "encrypted-tbn2.gstatic.com",
    "encrypted-tbn3.gstatic.com",
)

# Per-process set of URLs/paths we've already sent, to stop the model from
# shipping the same picture twice when it gets confused about what it did.
_ALREADY_SENT = set()

def _text_phone(msg: str) -> None:
    """If iMessage mobile mode is on, forward a short summary to Matt's phone."""
    if not os.path.exists(_MOBILE_FLAG):
        return
    msg = (msg or "").strip()
    if len(msg) > 500:
        msg = msg[:497] + "..."
    try:
        subprocess.Popen(
            ["/bin/bash", _IMSG_SEND, msg],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

def _download(url: str, ext_hint: str = "") -> str:
    """Download a URL (including data: URIs) to _DL_DIR and return the local path."""
    os.makedirs(_DL_DIR, exist_ok=True)
    import hashlib, base64, mimetypes
    if url.startswith("data:"):
        header, b64 = url.split(",", 1)
        mime = header.split(";")[0][5:] or "image/jpeg"
        ext = mimetypes.guess_extension(mime) or ".jpg"
        data = base64.b64decode(b64)
        name = hashlib.md5(url[:200].encode()).hexdigest()[:10] + ext
        path = os.path.join(_DL_DIR, name)
        with open(path, "wb") as f: f.write(data)
        return path
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip()
    ext = ext_hint or mimetypes.guess_extension(ctype) or ".jpg"
    if not ext.startswith("."): ext = "." + ext
    name = hashlib.md5(url.encode()).hexdigest()[:10] + ext
    path = os.path.join(_DL_DIR, name)
    with open(path, "wb") as f: f.write(data)
    return path

def _send_media_to_phone(url_or_path: str, kind: str = "image") -> str:
    """Download if URL, then send via the right iMessage script. Returns status."""
    if not os.path.exists(_MOBILE_FLAG):
        return "mobile mode is off"
    if not url_or_path:
        return "empty URL — provide a direct image URL"

    # Block junk thumbnail hosts — these are always fuzzy and useless.
    if kind == "image" and any(h in url_or_path for h in _BAD_IMAGE_HOSTS):
        return ("refused: that's a Google Images thumbnail (tiny/fuzzy). "
                "Use a direct image URL from Unsplash, LoremFlickr, Wikipedia, or Pexels instead.")

    # Dedupe: don't re-send the same URL in the same session.
    if url_or_path in _ALREADY_SENT:
        return "already sent this one — pick a different URL"

    try:
        if url_or_path.startswith(("http://", "https://", "data:")):
            path = _download(url_or_path)
        else:
            path = os.path.expanduser(url_or_path)
        # Reject garbage downloads (HTML error pages, 0-byte files).
        if not os.path.exists(path) or os.path.getsize(path) < 5000:
            return f"failed: download too small ({os.path.getsize(path) if os.path.exists(path) else 0} bytes) — not a real image"
        script = _IMSG_SEND_VID if kind == "video" else _IMSG_SEND_IMG
        subprocess.Popen(
            ["/bin/bash", script, path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _ALREADY_SENT.add(url_or_path)
        _ALREADY_SENT.add(path)
        return f"sent {os.path.basename(path)} ({_ALREADY_SENT.__len__() // 2} total this session)"
    except Exception as e:
        return f"failed: {type(e).__name__}: {e}"

async def _cdp_screenshot(cdp, path: str) -> str:
    """Capture a PNG of the current Brave page and save it."""
    import base64
    r = await cdp.cmd("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
    b64 = r.get("result", {}).get("data") or r.get("data", "")
    if not b64: return ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f: f.write(base64.b64decode(b64))
    return path

def _full_screenshot(path: str) -> str:
    """Capture the whole Mac desktop (all displays) via `screencapture`."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["/usr/sbin/screencapture", "-x", path], check=False, timeout=10)
    return path if os.path.exists(path) and os.path.getsize(path) > 1000 else ""

# ─── General-purpose system tools (shell, files) ────────────────────────────

def _tool_shell(cmd: str, timeout: int = 60) -> str:
    """Run a bash command. Returns stdout+stderr (truncated)."""
    if not cmd:
        return "empty command"
    try:
        r = subprocess.run(
            ["/bin/bash", "-lc", cmd],
            capture_output=True, text=True, timeout=timeout,
            cwd=os.path.expanduser("~"),
        )
        out = (r.stdout or "") + (r.stderr or "")
        out = out.strip() or f"(exit {r.returncode}, no output)"
        if len(out) > 3500:
            out = out[:3500] + f"\n...(truncated, exit {r.returncode})"
        else:
            out = out + (f"\n(exit {r.returncode})" if r.returncode != 0 else "")
        return out
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    except Exception as e:
        return f"shell error: {type(e).__name__}: {e}"

def _tool_read_file(path: str, max_bytes: int = 8000) -> str:
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return f"no such file: {path}"
    if os.path.isdir(path):
        try:
            entries = sorted(os.listdir(path))
        except Exception as e:
            return f"cannot list: {e}"
        return "DIR " + path + ":\n" + "\n".join(entries[:200])
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes + 1)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"binary file, {os.path.getsize(path)} bytes"
        if len(data) > max_bytes:
            text = text[:max_bytes] + "\n...(truncated)"
        return text
    except Exception as e:
        return f"read error: {type(e).__name__}: {e}"

def _tool_write_file(path: str, content: str) -> str:
    path = os.path.expanduser(path)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content or "")
        return f"wrote {len(content or '')} chars to {path}"
    except Exception as e:
        return f"write error: {type(e).__name__}: {e}"

# ─── Studio Record (Matt's local screen recorder API on :17494) ──────────────
_STUDIO_API = "http://127.0.0.1:17494"
_STUDIO_DIR = os.path.expanduser("~/Desktop/Screen Recordings")
_STUDIO_LAUNCHER = os.path.join(_STUDIO_DIR, "studio_record.py")
_STUDIO_VENV_PY = os.path.join(_STUDIO_DIR, ".venv/bin/python")

def _studio_up() -> bool:
    try:
        urllib.request.urlopen(f"{_STUDIO_API}/status", timeout=2)
        return True
    except Exception:
        return False

def _studio_ensure_running() -> None:
    if _studio_up():
        return
    if os.path.exists(_STUDIO_VENV_PY) and os.path.exists(_STUDIO_LAUNCHER):
        subprocess.Popen(
            [_STUDIO_VENV_PY, _STUDIO_LAUNCHER],
            cwd=_STUDIO_DIR,
            stdout=open("/tmp/studio_record.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        # Wait up to ~6s for the API to come up
        for _ in range(12):
            time.sleep(0.5)
            if _studio_up():
                break

def _studio_start(mode: str = "screen") -> str:
    _studio_ensure_running()
    try:
        req = urllib.request.Request(f"{_STUDIO_API}/start?mode={mode}", method="POST")
        urllib.request.urlopen(req, timeout=5).read()
        return f"recording ({mode})"
    except Exception as e:
        return f"start failed: {e}"

def _studio_stop_and_send(send: bool = True) -> str:
    try:
        req = urllib.request.Request(f"{_STUDIO_API}/stop", method="POST")
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        return f"stop failed: {e}"
    # Find the newest .mp4 in the Screen Recordings folder
    try:
        mp4s = [
            os.path.join(_STUDIO_DIR, f) for f in os.listdir(_STUDIO_DIR)
            if f.lower().endswith(".mp4")
        ]
        if not mp4s:
            return "stopped but no .mp4 found"
        newest = max(mp4s, key=os.path.getmtime)
        if send and os.path.exists(_MOBILE_FLAG):
            subprocess.Popen(
                ["/bin/bash", _IMSG_SEND_VID, newest],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return f"stopped + texting {os.path.basename(newest)}"
        return f"stopped: {newest}"
    except Exception as e:
        return f"stopped but send failed: {e}"

def _studio_status() -> str:
    try:
        with urllib.request.urlopen(f"{_STUDIO_API}/status", timeout=3) as r:
            return r.read().decode("utf-8", "ignore")[:300]
    except Exception as e:
        return f"studio not reachable: {e}"

# ─── Config ──────────────────────────────────────────────────────────────────

MLX_URL = os.environ.get("MLX_URL", "http://localhost:4000")
CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:9222")
MODEL = os.environ.get("MLX_MODEL_NAME", "claude-sonnet-4-6")
MAX_STEPS = int(os.environ.get("MAX_STEPS", "30"))

B, G, Y, R, D, BD, RS = "\033[94m", "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[1m", "\033[0m"

SYSTEM = """You are a general-purpose agent that helps the user accomplish ANY task — web work, code editing, shell commands, file reads/writes, running builds, deploying, sending media. Return ONE JSON tool call per response.

TOOLS (browser):
- navigate(url) — Go to URL
- snapshot() — Get page elements with UIDs. You RARELY need this: after navigate/click/type_text/scroll the fresh page is attached to the result automatically. Only call snapshot if you genuinely need to re-check the page without taking an action.
- click(uid) — Click element by UID from snapshot
- type_text(uid, text) — Type into a text field by UID
- scroll(direction) — "up" or "down"
- js(code) — Run JavaScript on the page. Return a VALUE (e.g. array of URLs).

TOOLS (system — use these for coding/building/deploy tasks):
- shell(cmd, timeout?) — Run any bash command on the Mac. Default cwd = $HOME. Use for: git, ssh, curl, wp-cli, python, npm, file ops, greps, finding files, running scripts, anything the user could type. DEFAULT FIRST CHOICE for non-web work.
- read_file(path) — Read a text file (or list a directory). Tilde (~) expands.
- write_file(path, content) — Overwrite a file with content. Creates parent dirs.

TOOLS (media to user's phone — requires mobile mode on):
- screenshot() — Capture the current Brave page as PNG and text it
- fullscreen_shot() — Capture the WHOLE Mac desktop (all displays) and text it
- send_image(url) — Download an image URL and text it
- send_video(url) — Download a video URL and text it
- record_start(mode) — Start Studio Record. mode = "screen" / "face" / "screen_face"
- record_stop() — Stop Studio Record and auto-text the .mp4

- done(message) — Task complete. Use this for conversational replies too (e.g. if the user asks "why did you X", answer via done()).

FORMAT: {"tool": "name", "args": {...}}
BATCHING: You may instead return a JSON ARRAY of up to 5 tool calls, e.g.
[{"tool":"type_text","args":{"uid":"12","text":"hi"}},{"tool":"click","args":{"uid":"15"}}]
They run in sequence and you get every result back numbered, with the fresh page
attached after the last one. Each reply from you costs seconds of thinking, so
BATCH whenever the next steps are obvious — filling several fields then clicking
submit is the classic case. ONE HARD RULE: uids only exist for the page you have
already seen. NEVER batch a click/type_text that targets a page you haven't seen
yet (e.g. don't navigate AND click in the same batch — navigate first, read the
attached page, then batch the rest).
RULES:
- FOLLOW THE USER'S TASK EXACTLY. Do what they asked — nothing else.
- PICK THE RIGHT TOOL FAMILY FIRST: if the task involves code/files/deploy/shell → use shell/read_file/write_file. Don't open a browser for things the terminal handles in one command.
- After navigate/click/type_text/scroll the new page state is ALREADY attached to the result — read it and act. Do NOT call snapshot as a separate step.
- Use snapshot UIDs to find the right elements — read the labels/text carefully.
- For forms: snapshot to find fields, type_text to fill them, click to submit.
- For navigation: click links/buttons that match what the user wants.
- NEVER click the same UID more than twice. Try a different approach.
- If the page hasn't changed after an action, try: scroll, js(code), or a different element.
- "Send image/photo/picture to my phone" — use send_image(url) with a REAL image URL.
  * **NEVER send URLs from encrypted-tbnN.gstatic.com** — those are Google Images thumbnails and show up fuzzy on the phone. The tool will reject them.
  * GOOD direct-image sources to navigate to FIRST, then scrape real src URLs:
    - https://loremflickr.com/1080/1350/KEYWORDS?lock=N   (one URL per lock number, no page visit needed)
    - https://unsplash.com/s/photos/KEYWORDS
    - https://commons.wikimedia.org/w/index.php?search=KEYWORDS
  * If the user asks for N pictures, send EXACTLY N distinct images. Count each successful send and STOP when you hit N.
  * Re-sending the same URL is rejected — if you see "already sent this one," move to a different URL.
- "Send screenshot" / "send me what the page looks like" = screenshot() (current Brave tab only).
- "Screenshot of my desktop / everything / the whole screen" = fullscreen_shot().
- "Record my screen" / "take a video of X" — record_start("screen"), do the thing, record_stop() (auto-texts the mp4).
- Do NOT call done until the user's task is actually finished. If the user asks a conversational question ("why did you..."), answer with done() — don't navigate.
- No explanations — just JSON tool calls."""

# ─── CDP ─────────────────────────────────────────────────────────────────────

class CDP:
    def __init__(self):
        self.ws = None; self.mid = 0

    async def connect(self):
        with urllib.request.urlopen(f"{CDP_URL}/json", timeout=5) as r:
            pages = json.loads(r.read())
        ws_url = next((p["webSocketDebuggerUrl"] for p in pages if p.get("type")=="page" and "devtools" not in p.get("url","")), None)
        if not ws_url: ws_url = pages[0]["webSocketDebuggerUrl"] if pages else None
        if not ws_url: print(f"{R}No browser pages{RS}"); sys.exit(1)
        self.ws = await websockets.connect(ws_url, max_size=50*1024*1024)
        for m in ["DOM.enable","Accessibility.enable","Page.enable","Runtime.enable"]: await self.cmd(m)

    async def reconnect(self):
        """Reconnect to the current active page after navigation."""
        try:
            if self.ws: await self.ws.close()
        except: pass
        await asyncio.sleep(1)
        with urllib.request.urlopen(f"{CDP_URL}/json", timeout=5) as r:
            pages = json.loads(r.read())
        ws_url = next((p["webSocketDebuggerUrl"] for p in pages if p.get("type")=="page" and "devtools" not in p.get("url","")), None)
        if ws_url:
            self.ws = await websockets.connect(ws_url, max_size=50*1024*1024)
            self.mid = 0
            for m in ["DOM.enable","Accessibility.enable","Page.enable","Runtime.enable"]: await self.cmd(m)

    async def cmd(self, method, params=None):
        self.mid += 1
        msg = {"id":self.mid,"method":method}
        if params: msg["params"] = params
        try:
            await self.ws.send(json.dumps(msg))
            while True:
                r = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
                if r.get("id") == self.mid:
                    return r.get("result", r.get("error", {}))
        except Exception:
            # Reconnect on broken connection (page navigated away)
            await self.reconnect()
            return {"error": "Connection lost, reconnected. Try again."}

    async def wait_ready(self, want_origin=None, cap=6.0):
        """Poll until the page is done loading, instead of always sleeping a
        fixed amount. Returns as soon as readyState is 'complete' (fast pages
        ~0.5s) or bails out at `cap` seconds. When navigating, `want_origin`
        guards against returning while the OLD page is still showing — we wait
        until the new origin is actually in effect before calling it ready."""
        await asyncio.sleep(0.15)  # brief buffer so we don't read the OLD page's readyState
        deadline = time.time() + cap
        while time.time() < deadline:
            r = await self.cmd("Runtime.evaluate", {"expression": "JSON.stringify([document.readyState, location.href])", "returnByValue": True})
            try:
                state, href = json.loads(r.get("result", {}).get("value", "") or "[]")
            except Exception:
                state, href = "", ""
            url_ok = (want_origin is None) or href.lower().startswith(want_origin) or href == "about:blank"
            if state == "complete" and url_ok:
                return
            await asyncio.sleep(0.1)

    async def navigate(self, url):
        from urllib.parse import urlparse
        await self.cmd("Page.navigate", {"url": url})
        p = urlparse(url)
        want_origin = f"{p.scheme}://{p.netloc}".lower() if p.scheme and p.netloc else None
        await self.wait_ready(want_origin)
        return f"Navigated to {url}"

    async def snapshot(self):
        tree = await self.cmd("Accessibility.getFullAXTree", {"max_depth": 8})
        nodes = tree.get("nodes", [])
        lines = []
        # Prioritize actionable elements: links, buttons, inputs, headings
        priority_roles = {"link","button","textbox","searchbox","heading","combobox","menuitem","checkbox","radio"}
        for n in nodes:
            role = n.get("role",{}).get("value","")
            name = n.get("name",{}).get("value","")
            nid = n.get("nodeId","")
            if not name or len(name) < 3: continue
            if role not in priority_roles and role != "StaticText": continue
            # Skip StaticText unless it's substantial
            if role == "StaticText" and len(name) < 30: continue
            p = [f"[{nid}]", role, f'"{name[:120]}"']
            lines.append(" ".join(p))
            if len(lines) >= 200: break
        return "\n".join(lines) if lines else "(Empty page)"

    async def click(self, uid):
        uid = str(uid).strip("[]")
        r = await self.cmd("DOM.resolveNode", {"backendNodeId": int(uid)})
        if "error" in r: return f"Error: {r['error']}"
        oid = r.get("object",{}).get("objectId")
        if not oid: return "Error: can't resolve"
        await self.cmd("Runtime.callFunctionOn",{"objectId":oid,"functionDeclaration":"function(){this.scrollIntoView({block:'center'})}"})
        await asyncio.sleep(0.08)
        box = await self.cmd("DOM.getBoxModel",{"objectId":oid})
        if "error" in box or "model" not in box:
            await self.cmd("Runtime.callFunctionOn",{"objectId":oid,"functionDeclaration":"function(){this.click()}"})
            return "Clicked(JS)"
        c = box["model"]["content"]; x=(c[0]+c[4])/2; y=(c[1]+c[5])/2
        await self.cmd("Input.dispatchMouseEvent",{"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1})
        await self.cmd("Input.dispatchMouseEvent",{"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1})
        return "Clicked"

    async def type_into(self, uid, text):
        await self.click(uid); await asyncio.sleep(0.12)
        # FAST PATH: insert the whole string in one CDP call. Fires proper input
        # events, works in normal inputs, textareas, and contenteditable/rich
        # editors. ~50x faster than char-by-char for any real text.
        r = await self.cmd("Input.insertText", {"text": text})
        if not (isinstance(r, dict) and "error" in r):
            # Nudge search-as-you-type / React listeners that only react to keys.
            await self.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "ArrowLeft"})
            await self.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "ArrowLeft"})
            await self.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "ArrowRight"})
            await self.cmd("Input.dispatchKeyEvent", {"type": "keyUp", "key": "ArrowRight"})
            return f"Typed {len(text)} chars"
        # FALLBACK: per-character key events for fields that reject insertText.
        for ch in text:
            await self.cmd("Input.dispatchKeyEvent",{"type":"keyDown","text":ch,"key":ch})
            await self.cmd("Input.dispatchKeyEvent",{"type":"keyUp","key":ch})
        return f"Typed {len(text)} chars (keys)"

    async def scroll(self, d="down"):
        delta = -500 if d=="up" else 500
        await self.cmd("Input.dispatchMouseEvent",{"type":"mouseWheel","x":400,"y":400,"deltaX":0,"deltaY":delta})
        await asyncio.sleep(0.15); return f"Scrolled {d}"

    async def js(self, code):
        r = await self.cmd("Runtime.evaluate",{"expression":code,"returnByValue":True,"awaitPromise":True})
        if "error" in r: return f"Error: {r['error']}"
        return str(r.get("result",{}).get("value", r.get("result",{}).get("description","")))[:2000]

    async def post_comment(self, text):
        """Auto-handle commenting on any page.
        Uses DOM.pierce + DOM.focus + Input.insertText — works through
        cross-origin iframes, Shadow DOM, and ProseMirror editors.
        """
        # Step 1: Click Comments button
        print(f"  {D}→ Clicking Comments button...{RS}")
        await self.cmd("Runtime.evaluate",{"expression":"""
            const btn=Array.from(document.querySelectorAll('button')).find(b=>/comment/i.test(b.textContent));
            if(btn){btn.scrollIntoView({block:'center'});btn.click()}
        """})
        await asyncio.sleep(3)

        # Step 2: Wait for widget to load (don't scroll — it breaks Yahoo's infinite scroll)
        print(f"  {D}→ Loading comment widget...{RS}")
        await asyncio.sleep(5)

        # Step 3: Connect to OpenWeb iframe target and use DOM.pierce there
        # Save current URL so we can scroll back
        article_url = await self.js("document.URL")

        print(f"  {D}→ Searching for comment iframe...{RS}")
        for attempt in range(8):
            with urllib.request.urlopen(f"{CDP_URL}/json",timeout=5) as r:
                targets = json.loads(r.read())
            ow = [t for t in targets if t.get("type")=="iframe"
                  and any(k in t.get("url","") for k in ["openweb","spot.im","disqus","comment"])
                  and t.get("webSocketDebuggerUrl")]
            if ow: break
            # Small scroll only — don't trigger infinite scroll
            await self.cmd("Runtime.evaluate",{"expression":"window.scrollBy(0,150)"})
            await asyncio.sleep(2)
        else:
            # No comment iframe found
            pass

        if ow:
            print(f"  {D}→ Found iframe, connecting...{RS}")
            iws = await websockets.connect(ow[0]["webSocketDebuggerUrl"], max_size=50*1024*1024)
            imid = [0]
            async def isend(m,p=None):
                imid[0]+=1; msg={"id":imid[0],"method":m}
                if p: msg["params"]=p
                await iws.send(json.dumps(msg))
                while True:
                    r=json.loads(await asyncio.wait_for(iws.recv(),timeout=15))
                    if r.get("id")==imid[0]: return r.get("result",r.get("error",{}))

            for m in ["DOM.enable","Runtime.enable","Input.enable"]: await isend(m)
            await isend("DOM.getDocument",{"depth":-1,"pierce":True})

            # Search inside the iframe (pierces Shadow DOM)
            for attempt in range(5):
                await isend("DOM.getDocument",{"depth":-1,"pierce":True})
                r = await isend("DOM.performSearch",{"query":".ProseMirror","includeUserAgentShadowDOM":True})
                count = r.get("resultCount",0)
                sid = r.get("searchId","")
                if count > 0:
                    results = await isend("DOM.getSearchResults",{"searchId":sid,"fromIndex":0,"toIndex":count})
                    nid = results.get("nodeIds",[])[0]
                    fr = await isend("DOM.focus",{"nodeId":nid})
                    if "error" not in fr:
                        # Critical: wait for editor to be ready
                        await asyncio.sleep(1)
                        print(f"  {D}→ Typing comment ({len(text)} chars)...{RS}")
                        await isend("Input.insertText",{"text":text})
                        await asyncio.sleep(0.5)
                        if sid: await isend("DOM.discardSearchResults",{"searchId":sid})
                        await iws.close()
                        # Scroll comment area into view on main page
                        print(f"  {D}→ Scrolling to comment...{RS}")
                        await self.cmd("Runtime.evaluate",{"expression":"""
                            const iframes=document.querySelectorAll('iframe');
                            for(const f of iframes){if(f.src&&f.src.includes('openweb')){f.scrollIntoView({block:'center',behavior:'instant'});break}}
                        """})
                        await asyncio.sleep(0.3)
                        # Scroll up a bit so the comment input is visible, not just the iframe top
                        await self.cmd("Runtime.evaluate",{"expression":"window.scrollBy(0,-150)"})
                        return f"{G}Comment drafted! ({len(text)} chars) — NOT posted, ready for review.{RS}"
                if sid: await isend("DOM.discardSearchResults",{"searchId":sid})
                # Wait for SpotIM to render
                print(f"  {D}→ Waiting for editor (attempt {attempt+1})...{RS}")
                await asyncio.sleep(3)

            await iws.close()

        # Fallback: simple main-page textarea
        escaped = text.replace("\\","\\\\").replace("'","\\'").replace("\n","\\n")
        r = await self.cmd("Runtime.evaluate",{"expression":f"""
            const el=document.querySelector('textarea,input[type=text],[contenteditable=true]');
            el?(el.focus(),el.value?el.value='{escaped}':el.innerText='{escaped}','found'):'none'
        ""","returnByValue":True})
        if r.get("result",{}).get("value")=="found":
            return f"{G}Comment drafted! ({len(text)} chars){RS}"

        return f"{Y}No comment input found. Comments may not be available on this page.{RS}"

    async def close(self):
        if self.ws: await self.ws.close()


# ─── MLX ─────────────────────────────────────────────────────────────────────

def ask_model(messages):
    body = json.dumps({"model":MODEL,"max_tokens":1024,"temperature":0.3,"system":SYSTEM,"messages":messages}).encode()
    req = urllib.request.Request(f"{MLX_URL}/v1/messages",data=body,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=120) as r: result=json.loads(r.read())
    return "".join(b.get("text","") for b in result.get("content",[]) if b.get("type")=="text")

def generate_comment(article_text):
    """Generate a clean comment from article text. Handles Qwen's verbose reasoning."""
    body = json.dumps({
        "model": MODEL, "max_tokens": 1024, "temperature": 0.7,
        "system": "Comment on the news article. 2-3 sentences.",
        "messages": [{"role": "user", "content": article_text}]
    }).encode()
    req = urllib.request.Request(f"{MLX_URL}/v1/messages", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        result = json.loads(r.read())
    raw = "".join(b.get("text", "") for b in result.get("content", []) if b.get("type") == "text")
    text = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    text = re.sub(r'\*+', '', text)  # Remove markdown

    # Qwen ALWAYS dumps reasoning. Extract only real comment sentences.
    all_sentences = re.findall(r'([A-Z][^.!?]{20,}[.!?])', text)

    # Filter out meta-reasoning — NOT part of a real comment
    meta = ['draft','constraint','sentence','critique','user','task','goal',
            'checking','format','plain text','let me','let\'s','count',
            'analyze','request','input','output','concise','polish',
            'revised','alternative','stick to','meets','criteria',
            'thinking','process','step','final','make sure']
    real = [s.strip() for s in all_sentences
            if not any(w in s.lower() for w in meta) and len(s) > 30]

    if real:
        return ' '.join(real[-3:])

    return "This situation raises serious concerns that demand greater transparency."


def parse(text):
    text = re.sub(r'<think>.*?</think>','',text,flags=re.DOTALL).strip()
    start = text.find('{"tool"')
    if start<0: start=text.find('{ "tool"')
    if start>=0:
        d=0
        for i in range(start,len(text)):
            if text[i]=='{': d+=1
            elif text[i]=='}':
                d-=1
                if d==0:
                    try: return json.loads(text[start:i+1])
                    except: break
    for m in re.finditer(r'\{[^{}]+\}',text):
        try:
            o=json.loads(m.group(0))
            if "tool" in o: return o
        except: continue
    return None


def parse_multi(text):
    """Extract one OR several tool calls. A JSON array of calls = a batch
    (executed in order, one model turn). Falls back to the single-call parser."""
    clean = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    start = clean.find('[')
    if start >= 0:
        d = 0
        for i in range(start, len(clean)):
            if clean[i] == '[': d += 1
            elif clean[i] == ']':
                d -= 1
                if d == 0:
                    try:
                        arr = json.loads(clean[start:i+1])
                        if isinstance(arr, list):
                            calls = [o for o in arr if isinstance(o, dict) and "tool" in o]
                            if calls:
                                return calls[:5]
                    except Exception:
                        pass
                    break
    tc = parse(text)
    return [tc] if tc else []


# ─── Agent ───────────────────────────────────────────────────────────────────

async def run(task):
    cdp = CDP(); await cdp.connect()
    print(f"{G}Connected to Brave{RS}\n")

    messages = [{"role":"user","content":f"Task: {task}"}]
    click_counts = {}  # Track how many times each UID is clicked
    last_snapshot = ""  # Track last snapshot to detect stuck state

    async def exec_one(tool, args):
        """Execute a single tool call. Returns a result string, or the tuple
        ('__DONE__', message) when the model calls done()."""
        if tool=="navigate": return await cdp.navigate(args.get("url",""))
        if tool=="snapshot": return await cdp.snapshot()
        if tool=="click": return await cdp.click(args.get("uid",""))
        if tool=="type_text": return await cdp.type_into(args.get("uid",""),args.get("text",""))
        if tool=="scroll": return await cdp.scroll(args.get("direction","down"))
        if tool=="comment": return await cdp.post_comment(args.get("text",""))
        if tool=="js": return await cdp.js(args.get("code",""))
        if tool=="screenshot":
            path = os.path.join(_DL_DIR, f"page_{int(time.time())}.png")
            saved = await _cdp_screenshot(cdp, path)
            return _send_media_to_phone(saved, kind="image") if saved else "screenshot capture failed"
        if tool=="fullscreen_shot":
            path = os.path.join(_DL_DIR, f"desktop_{int(time.time())}.png")
            saved = _full_screenshot(path)
            return _send_media_to_phone(saved, kind="image") if saved else "screencapture failed"
        if tool=="send_image": return _send_media_to_phone(args.get("url",""), kind="image")
        if tool=="send_video": return _send_media_to_phone(args.get("url",""), kind="video")
        if tool=="record_start": return _studio_start(args.get("mode","screen"))
        if tool=="record_stop": return _studio_stop_and_send(send=True)
        if tool=="record_status": return _studio_status()
        if tool=="shell": return _tool_shell(args.get("cmd",""), int(args.get("timeout", 60) or 60))
        if tool=="read_file": return _tool_read_file(args.get("path",""))
        if tool=="write_file": return _tool_write_file(args.get("path",""), args.get("content",""))
        if tool=="done": return ("__DONE__", args.get("message",""))
        return f"Unknown: {tool}"

    for step in range(1, MAX_STEPS+1):
        t0=time.time(); resp=ask_model(messages); elapsed=time.time()-t0
        calls = parse_multi(resp)
        if not calls:
            print(f"  {D}Step {step} (no tool) {elapsed:.1f}s{RS}")
            messages.append({"role":"assistant","content":resp})
            messages.append({"role":"user","content":'Respond with ONLY: {"tool":"name","args":{...}} or a JSON array of such calls'})
            continue

        # ─── Execute the call(s) — a batch runs back-to-back on ONE model turn ──
        results = []
        page_changed = False
        loop_hit = False
        for ci, tc in enumerate(calls):
            tool=tc.get("tool",""); args=tc.get("args",{}) or {}

            # ─── Loop Detection ─────────────────────────────────────────
            if tool == "click":
                uid = str(args.get("uid", ""))
                click_counts[uid] = click_counts.get(uid, 0) + 1
                if click_counts[uid] > 2:
                    print(f"  {Y}Step {step} LOOP DETECTED: uid {uid} clicked {click_counts[uid]} times — forcing Escape + snapshot{RS}")
                    # Auto-recover: press Escape (closes lightboxes/overlays), then force a snapshot
                    await cdp.js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
                    await asyncio.sleep(0.5)
                    snap = await cdp.snapshot()
                    messages.append({"role":"assistant","content":json.dumps(tc)})
                    messages.append({"role":"user","content":f"Result: LOOP DETECTED — you clicked uid {uid} {click_counts[uid]} times. The page hasn't changed. I pressed Escape to close any overlay. Here is a fresh snapshot — try a DIFFERENT approach:\n\n{snap[:3000]}"})
                    loop_hit = True
                    break

            tag = f"{ci+1}/{len(calls)} " if len(calls) > 1 else ""
            args_s=', '.join(f'{k}={repr(v)[:40]}' for k,v in args.items())
            print(f"  {D}Step {step}{RS} {tag}{B}{tool}{RS}({args_s}) {D}{elapsed:.1f}s{RS}")
            elapsed = 0.0  # model time is only shown on the first action of a batch

            r = await exec_one(tool, args)
            if isinstance(r, tuple) and r[0] == "__DONE__":
                done_msg = r[1]
                print(f"\n{G}{BD}Done:{RS} {done_msg}")
                _text_phone(f"✅ {done_msg}")
                await cdp.close(); return

            if tool == "snapshot":
                # Detect stuck state: snapshot looks the same as last time
                if last_snapshot and r == last_snapshot:
                    r = r + "\n\n⚠️ WARNING: This snapshot is IDENTICAL to the previous one. The page has NOT changed. Try a different approach — scroll, press Escape, or navigate to a different URL."
                last_snapshot = r

            if tool in ("navigate", "click", "type_text", "scroll"):
                page_changed = True
            results.append(f"[{ci+1}] {tool}: {r}" if len(calls) > 1 else str(r))
            print(f"         {D}→ {str(r)[:100].replace(chr(10),' ')}{RS}")

            # An error mid-batch invalidates the model's plan — stop, report, let it re-think.
            if isinstance(r, str) and r.startswith("Error") and ci + 1 < len(calls):
                results.append(f"(batch stopped: action {ci+1} errored, skipped the remaining {len(calls)-ci-1})")
                break

        if loop_hit:
            continue

        r = "\n".join(results)

        # Auto-attach a fresh page view after any action that changes the page,
        # so the model never has to spend a separate (slow) turn calling snapshot.
        # For a batch this happens ONCE, after the last action — not per action.
        if page_changed:
            snap = await cdp.snapshot()
            if last_snapshot and snap == last_snapshot:
                snap += "\n\n⚠️ Page UNCHANGED since the last action — try a different element or approach."
            last_snapshot = snap
            r = f"{r}\n\nPAGE NOW (already snapshotted for you — do NOT call snapshot):\n{snap}"

        if len(r)>4000: r=r[:4000]+"...(truncated)"
        messages.append({"role":"assistant","content":json.dumps(calls if len(calls)>1 else calls[0])})
        messages.append({"role":"user","content":f"Result: {r}"})
        if len(messages)>10: messages=messages[:1]+messages[-8:]

    print(f"\n{Y}Reached max steps ({MAX_STEPS}). Task may be incomplete.{RS}")
    _text_phone("⚠️ Ran out of steps before finishing. Send a narrower ask?")
    await cdp.close()

def main():
    print(f"\n{BD}  → Local Browser Agent{RS}")
    print(f"  {D}MLX + CDP · iframes + Shadow DOM · no cloud{RS}\n")

    # If args passed, run once
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        print()
        try:
            asyncio.run(run(task))
        except Exception as e:
            print(f"\n{R}Task failed: {type(e).__name__}: {e}{RS}")
        return

    # Interactive loop — keep running tasks. Catch ANY exception from a single
    # task (MLX timeout, CDP websocket drop, bad model output, etc.) and loop
    # back to the prompt instead of exiting — one bad task shouldn't kill the
    # whole session.
    while True:
        try:
            task = input(f"\n{BD}What should I do?{RS} ")
        except (KeyboardInterrupt, EOFError):
            print(f"\n{D}Bye!{RS}")
            break
        if not task.strip(): continue
        if task.strip().lower() in ("quit","exit","q"): break
        print()
        try:
            asyncio.run(run(task))
        except KeyboardInterrupt:
            print(f"\n{Y}Task interrupted — back to prompt{RS}")
            _text_phone("⏹️ Task interrupted. Ready for the next one.")
        except Exception as e:
            print(f"\n{R}Task failed: {type(e).__name__}: {e}{RS}")
            print(f"{D}Back to prompt — try again or type 'quit' to exit{RS}")
            _text_phone(f"❌ Task failed: {type(e).__name__}. Send another?")

if __name__=="__main__": main()
