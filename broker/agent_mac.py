"""Agent Mac browsing: do logged-in browser jobs inside the virtual Mac, off Matt's screen.

Matt (2026-11-13): agent tabs kept landing on his screen while he worked ("fighting over
the screen"). The virtual Mac (terminal-mac guest, see bb-desktop) has its own Brave, so
jobs can run there instead. It has none of his logins, so this copies ONE site's sign-in
cookies from his Brave into it at the moment a job needs that site ("copy my logins over...
when I need them, not right now"), never all of them at once.

    from agent_mac import agent_mac_tab, copy_login
    with agent_mac_tab("woo-check", "https://ineedhemp.com/wp-admin/", purpose="...") as tab:
        tab.eval("document.title")          # same Tab API as broker_client.lease()

    bb-login-copy <site> [...]          copy just those sites' logins (the VM must be leased)

Some sites are never copied: banks, government sites and Authorize.net allow one session
at a time, and a second copy can sign him out (see the 9/28 CDTFA lesson). Cookie values
are never printed or logged, only counts.
"""
import json, os, re, socket, subprocess, sys, time
from contextlib import contextmanager
from urllib.request import urlopen, Request
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from websocket import create_connection          # noqa: E402
import broker_client as bc                       # noqa: E402

HOST_CDP = os.environ.get("BB_HOST_CDP", "http://127.0.0.1:9222")   # Matt's Brave (via the broker proxy)
GUEST_SSH = os.environ.get("BB_GUEST_SSH", "agentmac")              # ~/.ssh/config alias for the guest
GUEST_PORT = 9222                                                   # guest Brave's CDP, on the guest's localhost
GUEST_PROFILE = "$HOME/agent-brave"
GUEST_BRAVE = "/Applications/Brave Browser.app"
LOG = os.path.join(HERE, "desktop.log")

NEVER_COPY = [
    r"(^|\.)gov$", r"(^|\.)ca\.gov$", r"authorize\.net$", r"cdtfa", r"irs\.gov",
    r"chase\.com$", r"wellsfargo\.com$", r"bankofamerica\.com$", r"capitalone\.com$",
    r"americanexpress\.com$", r"citi\.com$", r"usbank\.com$", r"schwab\.com$",
    r"fidelity\.com$", r"vanguard\.com$", r"paypal\.com$", r"coinbase\.com$",
    r"(^|\.)bank", r"creditunion", r"(^|\.)fcu\.",
]


class NeverCopy(ValueError):
    """The site is on the never-copy list (one-session sites). Use his real Brave, ask first."""


def _log(line):
    with open(LOG, "a") as f:
        f.write(time.strftime("[%Y-%m-%dT%H:%M:%S] ") + line + "\n")


def site_of(url_or_site):
    """'https://www.ineedhemp.com/wp-admin/' -> 'ineedhemp.com' (last two labels, or three for co.uk-style)."""
    h = urlparse(url_or_site).hostname if "://" in url_or_site else url_or_site.strip().lstrip(".")
    h = (h or "").lower()
    parts = h.split(".")
    if len(parts) > 2 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _check_allowed(site):
    for pat in NEVER_COPY:
        if re.search(pat, site):
            raise NeverCopy(f"{site} is a one-session site (bank, government or payments); "
                            "its login is never copied. Use his real Brave and ask him first.")


def _cdp(ws_url, method, **params):
    ws = create_connection(ws_url, timeout=30, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": method, "params": params}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error'].get('message')}")
                return m.get("result", {})
    finally:
        ws.close()


def _browser_ws(base):
    return json.load(urlopen(base + "/json/version", timeout=10))["webSocketDebuggerUrl"]


def _matches(cookie_domain, site):
    d = cookie_domain.lstrip(".").lower()
    return d == site or d.endswith("." + site)


def _clean(c):
    keep = ("name", "value", "domain", "path", "secure", "httpOnly", "sameSite", "priority",
            "sourceScheme", "sourcePort", "partitionKey")
    out = {k: c[k] for k in keep if k in c and c[k] is not None}
    if not c.get("session") and c.get("expires", -1) > 0:
        out["expires"] = c["expires"]
    return out


# ---- the guest side: its Brave and a private tunnel to its CDP port ----

def _guest_ip(vm="default"):
    """The guest's address from macOS's DHCP leases (it can change after a cold boot)."""
    try:
        mac = open(os.path.expanduser(f"~/Library/Application Support/terminal-mac/machines/{vm}/mac-address")).read().strip()
        mac = ":".join(x.lstrip("0") or "0" for x in mac.split(":"))
        block = open("/var/db/dhcpd_leases").read().split("}")
        for b in block:
            if f"hw_address=1,{mac}" in b:
                m = re.search(r"ip_address=([\d.]+)", b)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def _ssh_args():
    ip = _guest_ip()
    return ["ssh", *(["-o", f"HostName={ip}"] if ip else []), GUEST_SSH]


def _ssh(cmd, timeout=60):
    return subprocess.run([*_ssh_args(), cmd], capture_output=True, text=True, timeout=timeout)


def ensure_guest_brave():
    """Start the guest's Brave (own profile, CDP on the guest's localhost) if it is not up."""
    check = f"curl -s -m 3 localhost:{GUEST_PORT}/json/version >/dev/null && echo up"
    if "up" in _ssh(check).stdout:
        return
    _ssh(f'open -na "{GUEST_BRAVE}" --args --remote-debugging-port={GUEST_PORT} '
         f'--user-data-dir={GUEST_PROFILE} --no-first-run --no-default-browser-check about:blank')
    for _ in range(30):
        time.sleep(1)
        if "up" in _ssh(check).stdout:
            return
    raise RuntimeError("the virtual Mac's Brave did not start (check it with bb-desktop peek)")


@contextmanager
def guest_tunnel():
    """Forward a free local port to the guest Brave's CDP for the length of the block."""
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    args = _ssh_args()
    p = subprocess.Popen([args[0], "-N", "-o", "ExitOnForwardFailure=yes",
                          "-L", f"127.0.0.1:{port}:127.0.0.1:{GUEST_PORT}", *args[1:]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                urlopen(base + "/json/version", timeout=1); break
            except Exception:
                if p.poll() is not None:
                    raise RuntimeError("could not open the tunnel to the virtual Mac")
                time.sleep(0.2)
        yield base
    finally:
        p.terminate()


# ---- the copy ----

def copy_login(*sites, guest_base=None):
    """Copy the sign-in cookies for these sites from Matt's Brave into the guest's Brave.
    Returns {site: count}. Never prints or logs a cookie value."""
    sites = [site_of(s) for s in sites]
    for s in sites:
        _check_allowed(s)
    allc = _cdp(_browser_ws(HOST_CDP), "Storage.getCookies").get("cookies", [])
    picked = {s: [_clean(c) for c in allc if _matches(c["domain"], s)] for s in sites}

    def push(base):
        ws = _browser_ws(base)
        for s, cs in picked.items():
            if cs:
                _cdp(ws, "Storage.setCookies", cookies=cs)

    if guest_base:
        push(guest_base)
    else:
        ensure_guest_brave()
        with guest_tunnel() as base:
            push(base)
    counts = {s: len(cs) for s, cs in picked.items()}
    _log("login-copy " + ", ".join(f"{s}: {n} cookies" for s, n in counts.items()))
    return counts


@contextmanager
def agent_mac_tab(owner, url, purpose=None, ttl=1800, copy=True):
    """A browser tab inside the virtual Mac, logged in as Matt for that one site, with the
    same Tab API as broker_client.lease(). Holds the desktop lease for the whole job, so
    no other agent drives the guest meanwhile; closes the tab and releases at the end."""
    with bc.desktop_lease(owner, purpose=purpose or f"browse {site_of(url)}", ttl=ttl):
        ensure_guest_brave()
        with guest_tunnel() as base:
            if copy:
                copy_login(url, guest_base=base)
            req = Request(base + "/json/new?" + url, method="PUT")
            t = json.load(urlopen(req, timeout=15))
            tab = bc.Tab(t["webSocketDebuggerUrl"])
            tab.owner, tab.target_id, tab.where = owner, t["id"], "agent-mac"
            try:
                time.sleep(3)
                yield tab
            finally:
                try:
                    tab.close()
                finally:
                    try:
                        urlopen(base + "/json/close/" + t["id"], timeout=5)
                    except Exception:
                        pass


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip()); sys.exit(0)
    try:
        for s, n in copy_login(*sys.argv[1:]).items():
            print(f"{s}: copied {n} login cookies into the virtual Mac" if n
                  else f"{s}: Matt's Brave has no cookies for it (not signed in there?)")
    except NeverCopy as e:
        print(e, file=sys.stderr); sys.exit(5)
