"""Live check of the agent's eyes against a real browser + browser-broker, HIDDEN tab only.

Not part of the pytest run. With browser-broker on 9223 and the browser's own CDP port
on 9229 (used only to play the human, so the broker does not count it as the agent):

    python3 tests/live_hidden_tab_check.py

It leases one hidden background tab, never brings anything forward, and closes the tab
whatever happens."""
import asyncio, json, os, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("BROWSER_AGENT_VISIBLE", None)          # hidden lease, always
import agent                                            # noqa: E402
from websocket import create_connection                 # noqa: E402

UP = os.environ.get("UP", "9229")
PAGE = ("data:text/html,<title>agent live test</title><label>Email <input id=e name=email></label>"
        "<button id=b>Send</button>")
ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{detail}]"))


async def main():
    cdp = agent.CDP()
    await cdp.connect()
    tid = agent._BROKER.get("target_id")
    check("leased a hidden broker tab", bool(tid))
    try:
        await cdp.navigate(PAGE)
        before = await cdp.look()
        check("look reads the page", before["title"] == "agent live test", before)
        # the agent types: its own input must not count as the human
        await cdp.js("document.getElementById('e').focus()")
        await cdp.cmd("Input.insertText", {"text": "agent@example.com"})
        await asyncio.sleep(0.6)
        mine = await cdp.look()
        check("agent's own typing is not the human", agent.human_changes(before, mine) == [], mine)
        # the human types, through the browser's own port (a moment later than the agent:
        # input within AGENT_ECHO of the agent's own is counted as the agent's)
        await asyncio.sleep(agent.AGENT_ECHO + 0.5)
        h = create_connection(f"ws://127.0.0.1:{UP}/devtools/page/{tid}", suppress_origin=True, timeout=10)
        h.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                           "params": {"expression": "document.getElementById('e').select()"}})); h.recv()
        h.send(json.dumps({"id": 2, "method": "Input.insertText", "params": {"text": "matt@example.com"}})); h.recv()
        await asyncio.sleep(0.6)
        now = await cdp.look()
        changes = agent.human_changes(mine, now)
        check("the human's typing is noticed", agent.human_active(now) and any("matt@example.com" in c for c in changes), changes)
        await cdp.cmd("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a"})
        check("broker makes the agent wait while he types", (cdp.refused or "").startswith("browser-broker: the human"), cdp.refused)
        h.close()
    finally:
        await cdp.close()
        agent._release_tab()
        time.sleep(0.5)
        with urllib.request.urlopen(f"http://127.0.0.1:{UP}/json/list", timeout=5) as r:
            still = tid in [x["id"] for x in json.load(r)]
        check("release left his tab open for him (he was working in it)", still)
        if still:
            urllib.request.urlopen(f"http://127.0.0.1:{UP}/json/close/{tid}", timeout=5).read()
            print("cleanup: closed the test tab")


asyncio.run(main())
print(f"{sum(ok)}/{len(ok)} live checks passed")
sys.exit(0 if all(ok) else 1)
