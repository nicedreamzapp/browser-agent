#!/usr/bin/env python3
"""
Local Browser Agent — Direct MLX + Chrome DevTools Protocol.
Handles iframes, Shadow DOM, ProseMirror editors automatically.
"""

import json, os, re, sys, time, asyncio, websockets, urllib.request

# ─── Config ──────────────────────────────────────────────────────────────────

MLX_URL = os.environ.get("MLX_URL", "http://localhost:4000")
CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:9222")
MODEL = os.environ.get("MLX_MODEL_NAME", "claude-sonnet-4-6")
MAX_STEPS = int(os.environ.get("MAX_STEPS", "15"))

B, G, Y, R, D, BD, RS = "\033[94m", "\033[92m", "\033[93m", "\033[91m", "\033[2m", "\033[1m", "\033[0m"

SYSTEM = """You are a browser agent. Return ONE JSON tool call per response.

TOOLS:
- navigate(url) — Go to URL
- snapshot() — Get page elements with UIDs. Always do after navigate/click.
- click(uid) — Click element
- type_text(uid, text) — Type into element
- scroll(direction) — "up" or "down"
- js(code) — Run JavaScript
- done(message) — Task complete

FORMAT: {"tool": "name", "args": {...}}
RULES: After navigate, always snapshot. Be fast. No explanations, just JSON."""

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

    async def navigate(self, url):
        await self.cmd("Page.navigate", {"url": url}); await asyncio.sleep(3)
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
        r = await self.cmd("DOM.resolveNode", {"backendNodeId": int(uid)})
        if "error" in r: return f"Error: {r['error']}"
        oid = r.get("object",{}).get("objectId")
        if not oid: return "Error: can't resolve"
        await self.cmd("Runtime.callFunctionOn",{"objectId":oid,"functionDeclaration":"function(){this.scrollIntoView({block:'center'})}"})
        await asyncio.sleep(0.2)
        box = await self.cmd("DOM.getBoxModel",{"objectId":oid})
        if "error" in box or "model" not in box:
            await self.cmd("Runtime.callFunctionOn",{"objectId":oid,"functionDeclaration":"function(){this.click()}"})
            return "Clicked(JS)"
        c = box["model"]["content"]; x=(c[0]+c[4])/2; y=(c[1]+c[5])/2
        await self.cmd("Input.dispatchMouseEvent",{"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1})
        await self.cmd("Input.dispatchMouseEvent",{"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1})
        return "Clicked"

    async def type_into(self, uid, text):
        await self.click(uid); await asyncio.sleep(0.3)
        for ch in text:
            await self.cmd("Input.dispatchKeyEvent",{"type":"keyDown","text":ch,"key":ch})
            await self.cmd("Input.dispatchKeyEvent",{"type":"keyUp","key":ch})
        return f"Typed {len(text)} chars"

    async def scroll(self, d="down"):
        delta = -500 if d=="up" else 500
        await self.cmd("Input.dispatchMouseEvent",{"type":"mouseWheel","x":400,"y":400,"deltaX":0,"deltaY":delta})
        await asyncio.sleep(0.5); return f"Scrolled {d}"

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
            for attempt in range(3):
                for selector in [".ProseMirror","[contenteditable=true]","textarea","[role=textbox]"]:
                    r = await isend("DOM.performSearch",{"query":selector,"includeUserAgentShadowDOM":True})
                    count = r.get("resultCount",0)
                    sid = r.get("searchId","")
                    if count > 0:
                        results = await isend("DOM.getSearchResults",{"searchId":sid,"fromIndex":0,"toIndex":count})
                        for nid in results.get("nodeIds",[]):
                            fr = await isend("DOM.focus",{"nodeId":nid})
                            if "error" in fr: continue
                            await asyncio.sleep(0.3)
                            print(f"  {D}→ Typing comment ({len(text)} chars)...{RS}")
                            await isend("Input.insertText",{"text":text})
                            if sid: await isend("DOM.discardSearchResults",{"searchId":sid})
                            await iws.close()
                            # Scroll comment area into view
                            await asyncio.sleep(0.5)
                            await self.cmd("Runtime.evaluate",{"expression":"""
                                // Try multiple ways to find and scroll to the comment area
                                const selectors = ['iframe[src*=openweb]','iframe[src*=spot]','[data-spot-im-module-default-area]','#spotim-specific'];
                                for(const sel of selectors) {
                                    const el = document.querySelector(sel);
                                    if(el) { el.scrollIntoView({block:'start',behavior:'instant'}); window.scrollBy(0,-100); break; }
                                }
                                // Fallback: scroll to Comments button
                                if(!document.querySelector('iframe[src*=openweb]')) {
                                    const btn=Array.from(document.querySelectorAll('button')).find(b=>/comment/i.test(b.textContent));
                                    if(btn) { btn.scrollIntoView({block:'start',behavior:'instant'}); window.scrollBy(0,100); }
                                }
                            """})
                            return f"{G}Comment drafted! ({len(text)} chars) — NOT posted, ready for review.{RS}"
                    if sid: await isend("DOM.discardSearchResults",{"searchId":sid})
                # Wait for SpotIM to render
                await asyncio.sleep(3)
                await isend("DOM.disable"); await isend("DOM.enable")
                await isend("DOM.getDocument",{"depth":-1,"pierce":True})

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

    # Qwen ALWAYS reasons out loud. Extract the actual comment from its output.
    # It typically writes drafts like: *Draft 2:* Actual comment text here.
    # Find ALL draft-like content and take the last/best one
    drafts = re.findall(r'[Dd]raft\s*\d*[^:]*:\*?\s*(.+?)(?=\n\s*\*?\s*\*?(?:[DCcRrWw]|$))', text, re.DOTALL)
    for d in reversed(drafts):
        clean = re.sub(r'\*+', '', d).strip().strip('"')
        if len(clean) > 40 and not clean.startswith(('Two ', 'Three ', 'Stronger', 'Fits', 'Addresses')):
            return clean

    # Find quoted text (model sometimes puts final answer in quotes)
    quoted = re.findall(r'"([^"]{40,}[.!?])"', text)
    if quoted:
        return quoted[-1]

    # Find the last 2-3 complete sentences that aren't analysis
    all_sentences = re.findall(r'([A-Z][^*\n\d]{25,}[.!?])', text)
    # Filter out meta-sentences about the task
    real = [s for s in all_sentences if not any(w in s.lower() for w in ['draft','constraint','sentence','critique','user','task','goal','checking','revised','output','format','plain text'])]
    if real:
        return ' '.join(real[-3:])

    # Absolute fallback — just take last 2 sentences regardless
    if all_sentences:
        return ' '.join(all_sentences[-2:])

    return "This situation raises serious concerns that demand greater transparency and accountability."


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


# ─── Agent ───────────────────────────────────────────────────────────────────

async def run(task):
    cdp = CDP(); await cdp.connect()
    print(f"{G}Connected to Brave{RS}\n")

    # Detect if this is a comment task
    is_comment = any(w in task.lower() for w in ["comment","draft","reply"])
    comment_text = None
    if is_comment:
        for marker in ["draft:","comment:","text:"]:
            idx = task.lower().rfind(marker)
            if idx >= 0:
                comment_text = task[idx+len(marker):].strip().rstrip(".")
                if len(comment_text) > 20: break
                comment_text = None

    # Extract topic keywords for smart navigation
    topic_words = []
    for word in ["iran","trump","war","ukraine","china","russia","gaza","israel","economy","oil"]:
        if word in task.lower(): topic_words.append(word)

    # FAST PATH: If this is a "go to site + find article + comment" task, skip the model for navigation
    if is_comment and topic_words:
        topic = " ".join(topic_words)
        # Detect which site
        site_url = "https://news.yahoo.com"
        for site in ["yahoo","reddit","cnn","bbc","nytimes"]:
            if site in task.lower():
                if site == "yahoo": site_url = "https://news.yahoo.com"
                elif site == "reddit": site_url = "https://www.reddit.com"
                break

        print(f"  {D}Step 1{RS} {B}navigate{RS}({site_url})")
        await cdp.navigate(site_url)

        print(f"  {D}Step 2{RS} {B}find article{RS}(topic='{topic}')")
        r = await cdp.js(f"""
            const links = Array.from(document.querySelectorAll('a'));
            const article = links.find(a => {{
                const text = a.textContent.toLowerCase();
                const href = a.href || '';
                return text.length > 30 && (href.includes('article') || href.includes('/news/'))
                    && {' && '.join(f'text.includes("{w}")' for w in topic_words)};
            }});
            if(article) {{ article.click(); article.textContent.trim().substring(0,100) }}
            else {{ 'NOT_FOUND' }}
        """)

        if r and r != "NOT_FOUND":
            print(f"         {D}→ {r[:80]}{RS}")
            await asyncio.sleep(3)

            # Generate comment if needed
            if not comment_text:
                print(f"  {D}Step 3{RS} {B}generate comment{RS}")
                article_text = await cdp.js("document.title + '. ' + Array.from(document.querySelectorAll('p')).map(p=>p.innerText).filter(t=>t.length>40).slice(0,6).join(' ')")
                comment_text = generate_comment(article_text[:600])
                print(f"         {D}→ {comment_text[:80]}...{RS}")

            # Post comment
            print(f"  {D}Step 4{RS} {B}post comment{RS}")
            result = await cdp.post_comment(comment_text)
            print(f"  {result}")
            print(f"\n{G}{BD}Done!{RS}")
            await cdp.close()
            return
        else:
            print(f"         {D}→ No article found with topic '{topic}', falling back to model{RS}")

    messages = [{"role":"user","content":f"Task: {task}\n\nRULES:\n- Navigate to the site, then snapshot.\n- Find article links and click one.\n- After reaching an article page, call done immediately."}]

    for step in range(1, MAX_STEPS+1):
        t0=time.time(); resp=ask_model(messages); elapsed=time.time()-t0
        tc = parse(resp)
        if not tc:
            print(f"  {D}Step {step} (no tool) {elapsed:.1f}s{RS}")
            messages.append({"role":"assistant","content":resp})
            messages.append({"role":"user","content":'Respond with ONLY: {"tool":"name","args":{...}}'})
            continue

        tool=tc.get("tool",""); args=tc.get("args",{})
        args_s=', '.join(f'{k}={repr(v)[:40]}' for k,v in args.items())
        print(f"  {D}Step {step}{RS} {B}{tool}{RS}({args_s}) {D}{elapsed:.1f}s{RS}")

        if tool=="navigate": r=await cdp.navigate(args.get("url",""))
        elif tool=="snapshot": r=await cdp.snapshot()
        elif tool=="click": r=await cdp.click(str(args.get("uid","")))
        elif tool=="type_text": r=await cdp.type_into(str(args.get("uid","")),args.get("text",""))
        elif tool=="scroll": r=await cdp.scroll(args.get("direction","down"))
        elif tool=="comment": r=await cdp.post_comment(args.get("text",""))
        elif tool=="js": r=await cdp.js(args.get("code",""))
        elif tool=="done":
            # If this is a comment task, auto-comment before finishing
            if is_comment:
                # If no comment text provided, generate one from article content
                if not comment_text:
                    print(f"\n  {BD}Generating comment from article...{RS}")
                    article_text = await cdp.js("document.querySelector('article, main, [role=main]')?.innerText?.substring(0,500) || document.title")
                    comment_text = generate_comment(article_text)
                    print(f"  {D}Generated: {comment_text[:80]}...{RS}")

                print(f"\n  {BD}Auto-commenting on article...{RS}")
                result = await cdp.post_comment(comment_text)
                print(f"  {result}")
            print(f"\n{G}{BD}Done:{RS} {args.get('message','')}")
            await cdp.close(); return
        else: r=f"Unknown: {tool}"

        if len(r)>4000: r=r[:4000]+"...(truncated)"
        messages.append({"role":"assistant","content":json.dumps(tc)})
        messages.append({"role":"user","content":f"Result: {r}"})
        if len(messages)>10: messages=messages[:1]+messages[-8:]
        print(f"         {D}→ {r[:100].replace(chr(10),' ')}{RS}")

    # If we hit max steps on a comment task, try commenting on whatever page we're on
    if is_comment:
        if not comment_text:
            article_text = await cdp.js("document.title + '. ' + Array.from(document.querySelectorAll('p')).map(p=>p.innerText).filter(t=>t.length>40).slice(0,6).join(' ')")
            comment_text = generate_comment(article_text)
        print(f"\n  {BD}Auto-commenting on current page...{RS}")
        result = await cdp.post_comment(comment_text)
        print(f"  {result}")

    await cdp.close()

def main():
    print(f"\n{BD}  → Local Browser Agent{RS}")
    print(f"  {D}MLX + CDP · iframes + Shadow DOM · no cloud{RS}\n")

    # If args passed, run once
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        print(); asyncio.run(run(task))
        return

    # Interactive loop — keep running tasks
    while True:
        try:
            task = input(f"\n{BD}What should I do?{RS} ")
            if not task.strip(): continue
            if task.strip().lower() in ("quit","exit","q"): break
            print(); asyncio.run(run(task))
        except (KeyboardInterrupt, EOFError):
            print(f"\n{D}Bye!{RS}")
            break

if __name__=="__main__": main()
