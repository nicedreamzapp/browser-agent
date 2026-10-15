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


def show(url, owner=None, purpose=None, wait=None, dwell=None):
    """Put a page in front of the human, taking turns with every other agent.

    Use this instead of `open -a "Brave Browser" <url>` or AppleScript activate.
    Those go straight to macOS, around the proxy, and two agents doing it minutes
    apart is what throws his window back and forth. This queues.

    Returns the broker's reply. Raises RuntimeError if the screen was busy for
    longer than `wait` -- print the URL and move on rather than forcing it.
    """
    body = {"owner": owner or os.environ.get("BROKER_OWNER") or "anonymous", "url": url,
            "purpose": purpose}
    if wait is not None:
        body["wait"] = wait
    if dwell is not None:
        body["dwell"] = dwell
    try:
        return _post("/show", body)
    except HTTPError as e:
        if e.code == 409:
            d = json.loads(e.read() or b"{}")
            raise RuntimeError(
                f"screen busy: {d.get('held_by')} is showing {d.get('showing')} "
                f"for another {d.get('seconds_left')}s") from None
        raise


def stage():
    """Who has the screen right now, and what is on it."""
    with urlopen(BROKER + "/stage", timeout=10) as r:
        return json.load(r)
