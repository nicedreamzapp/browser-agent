#!/usr/bin/env python3
"""Client for browser-broker. This is what agents import instead of touching CDP.

    from broker_client import lease

    with lease("yahoo-reaper", url="https://mail.yahoo.com", purpose="inbox sweep") as tab:
        tab.eval("document.title")

The context manager holds the lease, auto-renews it while you work, and releases
the tab when the block exits — including on an exception, which is the whole
point: a crashed agent must not strand a tab.

Never reach for cdp.Tab.find() from an agent again. find() lands you on whatever
tab happens to match, which may be the one the human is reading.
"""
import json, threading, time
from contextlib import contextmanager
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from urllib.parse import urlencode

from websocket import create_connection

import os
BROKER = os.environ.get("BROKER_URL", "http://127.0.0.1:9223")
RENEW_EVERY = 60
TTL = 300


def _post(path, payload):
    req = Request(BROKER + path, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=15) as r:
        return json.load(r)


def status():
    with urlopen(BROKER + "/status", timeout=10) as r:
        return json.load(r)


class Tab:
    """A leased page. Same shape as cdp.Tab so agents port over easily."""

    def __init__(self, ws_url):
        self.ws = create_connection(ws_url, suppress_origin=True, timeout=30)
        self._id = 0

    def send(self, method, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message"))
                return msg.get("result", {})

    def goto(self, url, wait=3):
        self.send("Page.enable")
        self.send("Page.navigate", url=url)
        time.sleep(wait)

    def eval(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True)
        return r.get("result", {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


@contextmanager
def lease(owner, url=None, purpose=None, hidden=True, ttl=TTL):
    g = _post("/lease", {"owner": owner, "url": url, "purpose": purpose,
                         "hidden": hidden, "ttl": ttl})
    stop = threading.Event()

    def keepalive():
        # One failed renew used to end this thread for good, and the tab was
        # reaped out from under the agent a few minutes later. Keep trying; only
        # stop when the broker says the lease is gone.
        while not stop.wait(RENEW_EVERY):
            for attempt in range(5):
                try:
                    _post("/renew", {"lease_id": g["lease_id"], "ttl": ttl})
                    break
                except HTTPError as e:
                    if e.code == 404:
                        return
                except Exception:
                    pass
                if stop.wait(min(2 ** attempt, 15)):
                    return

    threading.Thread(target=keepalive, daemon=True).start()
    tab = None
    try:
        tab = Tab(g["ws_url"])
        yield tab
    finally:
        stop.set()
        if tab is not None:
            tab.close()
        try:
            _post("/release", {"lease_id": g["lease_id"]})
        except Exception:
            pass  # the reaper will collect it


class HumanIsWorking(RuntimeError):
    """The human is typing or clicking in that tab right now. Do not reload, reopen or
    close it. `page` is what it shows at this moment: continue from there."""

    def __init__(self, message, page=None):
        super().__init__(message)
        self.page = page or {}


class StopAndAsk(RuntimeError):
    """You have opened and closed this page too many times. Stop and ask the human
    in one line what is wrong. Retrying is what this exists to prevent."""


def show(url, owner=None, purpose=None, wait=None, dwell=None, reload=False):
    """Put a page in front of the human, taking turns with every other agent.

    Use this instead of `open -a "Brave Browser" <url>` or AppleScript activate.
    Those go straight to macOS, around the proxy, and two agents doing it minutes
    apart is what throws his window back and forth. This queues.

    `owner` is your job. The job owns one tab on one site until unshow(owner).
    Showing the same site again returns that tab AS IS (reply["already_open"]),
    with reply["page"] holding what it shows now: read it before acting. It is
    reloaded only with reload=True, and never while the human works in it.

    Raises RuntimeError if the screen was busy for longer than `wait` (print the
    URL and move on), HumanIsWorking (continue from e.page) or StopAndAsk.
    """
    body = {"owner": owner or os.environ.get("BROKER_OWNER") or "anonymous", "url": url,
            "purpose": purpose}
    if wait is not None:
        body["wait"] = wait
    if dwell is not None:
        body["dwell"] = dwell
    if reload:
        body["reload"] = True
    try:
        return _post("/show", body)
    except HTTPError as e:
        if e.code not in (409, 423, 429):
            raise
        d = json.loads(e.read() or b"{}")
        if e.code == 409:
            raise RuntimeError(
                f"screen busy: {d.get('held_by')} is showing {d.get('showing')} "
                f"for another {d.get('seconds_left')}s") from None
        if e.code == 423:
            raise HumanIsWorking(d.get("error", "the human is working in that tab"), d.get("page")) from None
        if e.code == 429:
            raise StopAndAsk(d.get("error", "stop and ask the human")) from None


def peek(match, chars=400):
    """Read-only state of every open tab matching `match` (a job owner, a lease owner,
    a target id, or part of a URL): url, title, text, filled fields (secrets masked)
    and human = {active, human_active_since, last_input_seconds_ago, inputs}.
    Call it before every step on a page the human might touch."""
    with urlopen(BROKER + "/peek?" + urlencode({"match": match, "chars": chars}), timeout=30) as r:
        return json.load(r)["tabs"]


def human_active(match):
    """True when the human is working, right now, in any tab matching `match`."""
    return any((t.get("human") or {}).get("active") for t in peek(match, chars=0))


def unshow(match):
    """Close the page(s) you put up with show(), by owner or URL substring.

    Rule: close every tab you opened the moment its task is done. Only tabs the
    broker opened are closed; the human's own tabs, and any tab he is working in
    right now, are left to him (reply["left_open"] says which and why)."""
    return _post("/unshow", {"match": match})


def stage():
    """Who has the screen right now, and what is on it."""
    with urlopen(BROKER + "/stage", timeout=10) as r:
        return json.load(r)
