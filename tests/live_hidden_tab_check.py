"""Live check of the five-rules broker against a real browser, HIDDEN tabs only.

Not part of the pytest run. Start a spare proxy next to your real one, then run it:

    BROKER_PROXY_PORT=19222 BROKER_PORT=19223 BROKER_LOG=/tmp/bblive/broker.log \
      BROKER_HUMAN_QUIET=3 python3 proxy.py &
    API=http://127.0.0.1:19223 UP=9229 QUIET=3 python3 tests/live_hidden_tab_check.py

Talks to a temporary proxy instance (API on $API, CDP on $CDP) and, to play the
human, to the real browser's own port ($UP) directly, so the broker does not see
that input as an agent's. Never calls /show, never activates anything, and closes
every tab it opens."""
import json, os, sys, time, urllib.request
from websocket import create_connection

API = os.environ["API"]; UP = os.environ["UP"]
results = []


def http(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class WS:
    def __init__(self, url):
        self.ws = create_connection(url, suppress_origin=True, timeout=10); self.i = 0

    def call(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.i:
                return m

    def ev(self, expr):
        return self.call("Runtime.evaluate", expression=expr, returnByValue=True)["result"].get("result", {}).get("value")


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


PAGE = ("data:text/html,<title>bb live test</title><form><label>Email <input id=e name=email></label>"
        "<input id=p type=password name=pw></form><p>hello from the live test</p>")
tid = None
try:
    code, l = http("POST", "/lease", {"owner": "bb-live-test", "url": PAGE, "hidden": True, "ttl": 120})
    check("lease a hidden tab", code == 200, l)
    tid = l["target_id"]
    time.sleep(1.5)
    human = WS(f"ws://127.0.0.1:{UP}/devtools/page/{tid}")      # plays the human: raw, not via the broker
    check("listener installed in the page", human.ev("typeof window.__bbHuman") == "object")
    check("binding present", human.ev("typeof window.__bbHumanEvent") == "function")

    # the human clicks the email box and types
    box = human.ev("(()=>{const r=document.getElementById('e').getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()")
    for t in ("mousePressed", "mouseReleased"):
        human.call("Input.dispatchMouseEvent", type=t, x=box[0], y=box[1], button="left", clickCount=1)
    human.call("Input.insertText", text="matt@example.com")
    human.ev("document.getElementById('p').value='secret'")
    time.sleep(0.8)
    code, p = http("GET", f"/peek?match={tid[:12]}")
    t = (p.get("tabs") or [{}])[0]
    check("peek sees the human working", (t.get("human") or {}).get("active") is True, t.get("human"))
    check("peek shows his typing in the fields", {"field": "Email", "value": "matt@example.com"} in (t.get("fields") or []), t.get("fields"))
    check("peek masks the password", {"field": "pw", "value": "(hidden)"} in (t.get("fields") or []), t.get("fields"))
    check("peek reads the text", "hello from the live test" in (t.get("text") or ""), t.get("text"))

    agent = WS(l["ws_url"])                                   # the agent's path, through the broker
    r = agent.call("Page.reload")
    check("agent reload refused while he works", "human is working" in json.dumps(r), r)
    r = agent.call("Input.insertText", text="x")
    check("agent typing refused while he works", "human is working" in json.dumps(r), r)
    check("his text survived", human.ev("document.getElementById('e').value") == "matt@example.com")

    # the listener follows the tab to a new document
    human.call("Page.navigate", url="data:text/html,<title>second</title><p>page two</p>")
    time.sleep(1.2)
    check("listener re-installed after navigation", human.ev("typeof window.__bbHuman") == "object")

    quiet = float(os.environ.get("QUIET", "3"))
    time.sleep(quiet + 0.5)
    code, p = http("GET", f"/peek?match={tid[:12]}")
    before = (p["tabs"][0].get("human") or {}).get("inputs")
    check("quiet again after the window", (p["tabs"][0].get("human") or {}).get("active") is False, p["tabs"][0].get("human"))
    r = agent.call("Input.dispatchMouseEvent", type="mousePressed", x=20, y=20, button="left", clickCount=1)
    agent.call("Input.dispatchMouseEvent", type="mouseReleased", x=20, y=20, button="left", clickCount=1)
    check("agent input allowed once he is quiet", "result" in r, r)
    time.sleep(0.8)
    code, p = http("GET", f"/peek?match={tid[:12]}")
    h = p["tabs"][0].get("human") or {}
    check("agent's own click not counted as the human", h.get("inputs") == before and not h.get("active"), h)
    agent.ws.close(); human.ws.close()

    code, r = http("POST", "/release", {"lease_id": l["lease_id"]})
    check("release closes the tab when nobody is in it", r.get("tab_closed") is True, r)
    time.sleep(0.5)
    with urllib.request.urlopen(f"http://127.0.0.1:{UP}/json/list", timeout=5) as resp:
        gone = tid not in [x["id"] for x in json.load(resp)]
    check("tab is gone from the browser", gone)
finally:
    if tid:                                   # whatever happened, leave no tab behind
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{UP}/json/list", timeout=5) as resp:
                if tid in [x["id"] for x in json.load(resp)]:
                    urllib.request.urlopen(f"http://127.0.0.1:{UP}/json/close/{tid}", timeout=5).read()
                    print("cleanup: closed the test tab")
        except Exception as e:
            print("cleanup error", e)
print(f"{sum(ok for _, ok in results)}/{len(results)} live checks passed")
sys.exit(0 if all(ok for _, ok in results) else 1)
