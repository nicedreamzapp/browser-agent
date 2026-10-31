"""scrub.py -- everything the Broker Recorder writes passes through here first.

The rule is allowlist, not denylist: recorder.py never copies raw CDP params
into a trajectory. It builds each record from named fields, and every string
in those fields goes through one of the functions below. Then, before a line is
written, deep_scrub() runs scrub_text() over every string in the record once
more as a second net.

What is removed (see tests/test_scrub.py for the adversarial cases):
  * values typed into, set on, or inserted into a sensitive field (password,
    card number / expiry / CVC, SSN, one-time codes, PINs, secrets, API keys,
    bank numbers, security answers, date of birth); the length is not kept either
  * cookies, Authorization / Set-Cookie / X-API-Key headers, bearer and basic
    credentials, JWTs, private keys, known API-key formats
  * URL userinfo, sensitive query / fragment parameters, long random-looking
    values anywhere in a URL (path, query, fragment)
  * emails, phone numbers, card-like digit runs and SSNs in any free text
  * long random-looking strings (session ids, tokens) in free text

Standard library only, Python 3.9 (what the broker runs on).
"""
import math
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, quote

REDACTED = "[REDACTED]"
EMAIL = "[EMAIL]"
PHONE = "[PHONE]"
CARD = "[CARD]"
SSN = "[SSN]"
TOKEN = "[TOKEN]"

# ---------------------------------------------------------------------------
# sensitive fields
# ---------------------------------------------------------------------------
SENSITIVE_TYPES = {"password"}
# autocomplete tokens (https://html.spec.whatwg.org/#autofill-field)
SENSITIVE_AUTOCOMPLETE = re.compile(
    r"(^|\s)(current-password|new-password|one-time-code|cc-[a-z-]+|"
    r"transaction-amount|bday[a-z-]*)(\s|$)", re.I)
# names, ids, labels, placeholders, aria-labels
SENSITIVE_NAME = re.compile(
    r"pass(word|wd|code|phrase|key)|(^|[^a-z])pass([^a-z]|$)|(^|[^a-z])pwd|(^|[^a-z])pin([^a-z]|$)|"
    r"(^|[^a-z])code([^a-z]|$)|digit|"
    r"otp|one[\W_]*time|2fa|mfa|totp|verif(y|ication)[\W_]*code|auth(entication|orization)?[\W_]*code|"
    r"security[\W_]*(code|answer|question)|sec[\W_]*code|"
    r"card|(^|[^a-z])cc([^a-z]|$)|cc[\W_]*(num|no|exp|csc|cvv|cvc|name)|cvv|cvc|csc|ccv|expir|exp[\W_]*(date|month|year|mm|yy)|"
    r"ssn|social[\W_]*sec|tax[\W_]*id|(^|[^a-z])tin([^a-z]|$)|(^|[^a-z])ein([^a-z]|$)|"
    r"routing|account[\W_]*(number|num|no)|acct|iban|swift|bic([^a-z]|$)|sort[\W_]*code|"
    r"secret|token|api[\W_]*key|private[\W_]*key|access[\W_]*key|client[\W_]*secret|"
    r"recovery|seed[\W_]*phrase|mnemonic|"
    r"birth|(^|[^a-z])dob([^a-z]|$)|passport|licen[cs]e[\W_]*(num|no)|driver",
    re.I)


def is_sensitive_field(f):
    """True when the field described by `f` must never have its value recorded.

    `f` is a dict with any of: type, autocomplete (ac), name, id, label,
    placeholder, aria (aria-label). Unknown is NOT sensitive here; callers
    that do not know the target apply looks_secret() to the text instead."""
    if not f:
        return False
    t = str(f.get("type") or "").lower()
    if t in SENSITIVE_TYPES:
        return True
    if f.get("sensitive") is True:
        return True
    ac = str(f.get("autocomplete") or f.get("ac") or "")
    if ac and SENSITIVE_AUTOCOMPLETE.search(ac):
        return True
    for k in ("name", "id", "label", "placeholder", "aria", "fname", "fid"):
        v = f.get(k)
        if v and SENSITIVE_NAME.search(str(v)):
            return True
    return False


# ---------------------------------------------------------------------------
# free text
# ---------------------------------------------------------------------------
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)
_AUTH_SCHEME = re.compile(r"\b(Bearer|Basic|Digest|Token|Negotiate)\s+[A-Za-z0-9._~+/=-]{6,}", re.I)
_HEADER_LINE = re.compile(
    r"\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-auth-token|x-csrf-token|"
    r"x-xsrf-token|api-key)\b(\s*[:=]\s*|\"\s*:\s*\")[^\n\r\"]*", re.I)
_KEYVAL = re.compile(
    r"\b((?:[a-z]*[_-])?(?:password|passwd|pwd|passcode|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"session[_-]?id|sessionid|sid|auth|cookie|otp|pin|cvv|cvc|ssn|credential)s?)"
    r"(\s*[:=]\s*|\"\s*:\s*\"|'\s*:\s*')([^\s,;&\"'}]+)", re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}(\.[A-Za-z0-9_-]*)?")
_KNOWN_KEYS = re.compile(
    r"\b(sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|sk_(?:live|test)_[A-Za-z0-9]{10,}|pk_(?:live|test)_[A-Za-z0-9]{10,}|"
    r"rk_(?:live|test)_[A-Za-z0-9]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
    r"ya29\.[0-9A-Za-z_-]{10,}|glpat-[0-9A-Za-z_-]{16,}|hf_[A-Za-z0-9]{20,}|npm_[A-Za-z0-9]{20,}|"
    r"shpat_[A-Fa-f0-9]{20,}|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,})")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+(@|%40|\s+at\s+)[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_SSN = re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)")
# 13-19 digits, optionally grouped by spaces or dashes: card numbers (Luhn not required: over-redact)
_CARD = re.compile(r"(?<![\d.])(?:\d[ -]?){12,18}\d(?![\d.])")
_PHONE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s.\-]?)?(?:\(\d{2,4}\)[\s.\-]?|\d{2,4}[\s.\-])\d{3,4}[\s.\-]?\d{3,4}(?![\w.])"
    r"|(?<![\w.])\+\d{8,15}(?![\w.])|(?<![\w.$])\d{10,11}(?![\w.])")
_TOKENISH = re.compile(r"[A-Za-z0-9_+/=.~-]{20,}")
MONEY = "[MONEY]"
ADDRESS = "[ADDRESS]"
NUMBER = "[NUMBER]"
NAME = "[NAME]"
_MONEY = re.compile(r"(?:[$€£¥]|US\$|C\$|A\$)\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{1,2})?\s?(?:USD|EUR|GBP|CAD|AUD|dollars)\b",
                    re.I)
_STREET = re.compile(
    r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9'.-]+\s+){1,4}"
    r"(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|Ct|Court|Pl|Place|Pkwy|Parkway|"
    r"Hwy|Highway|Cir|Circle|Ter|Terrace|Trl|Trail|Loop|Sq|Square|Row|Run|Pike)\b\.?"
    r"(?:\s*(?:#|Apt\.?|Suite|Ste\.?|Unit)\s*[\w-]+)?", re.I)
_PO_BOX = re.compile(r"\bP\.?\s?O\.?\s*Box\s+\d+", re.I)
_STATE_ZIP = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
_ZIP_PLUS4 = re.compile(r"\b\d{5}-\d{4}\b")
_LONG_DIGITS = re.compile(r"(?<![\w])\d(?:[\s-]?\d){9,}(?![\w])")
_TRACKING = re.compile(r"\b(?:1Z[0-9A-Z]{16}|[A-Z]{2}\d{9}[A-Z]{2}|T\d{10}|\d{12,22})\b")
_NAME_AFTER = re.compile(
    r"\b((?:Hi|Hello|Hey|Dear|Thanks|Thank you|Regards|Cheers|Sincerely)[,]?\s+"
    r"|(?:From|To|Cc|Bcc|Reply-To|Sender|Recipient|Attn|Attention|Name|Customer|Ship to|Bill to|"
    r"Shipping to|Billing to|Sold to|Deliver to|Signed by)\s*:\s*)"
    r"([A-Z][a-zA-Z'\u2019-]+(?:\s+[A-Z][a-zA-Z'\u2019.-]*){0,3})")


def _entropy(s):
    if not s:
        return 0.0
    n = len(s)
    counts = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def looks_random(s, min_len=20):
    """A session id, token or key: long, mixed letters and digits, high entropy.

    Ordinary words, slugs ("how-to-clean-your-heater") and dates are not."""
    s = s.strip("=")
    if len(s) < min_len:
        return False
    if re.fullmatch(r"[0-9a-fA-F]{32,}", s):
        return True                          # hex digests, session ids
    has_digit = any(c.isdigit() for c in s)
    has_alpha = any(c.isalpha() for c in s)
    if not (has_digit and has_alpha):
        return False
    # slugs and sentences: mostly lowercase words split by - or _
    parts = re.split(r"[-_./~]", s)
    if len(parts) >= 3 and all(len(p) <= 12 for p in parts) and sum(c.isdigit() for c in s) / len(s) < 0.3:
        return False
    return _entropy(s) >= 3.6


def _tokens(m):
    w = m.group(0)
    return TOKEN if looks_random(w) else w


def scrub_text(s, max_len=None):
    """Mask secrets and personal data in free text. Safe to call twice."""
    if s is None:
        return None
    if not isinstance(s, str):
        s = str(s)
    s = _PRIVATE_KEY.sub(REDACTED, s)
    s = _HEADER_LINE.sub(lambda m: m.group(1) + m.group(2) + REDACTED, s)
    s = _AUTH_SCHEME.sub(lambda m: m.group(1) + " " + REDACTED, s)
    s = _JWT.sub(TOKEN, s)
    s = _KNOWN_KEYS.sub(TOKEN, s)
    s = _KEYVAL.sub(lambda m: m.group(1) + m.group(2) + REDACTED, s)
    s = _EMAIL.sub(EMAIL, s)
    s = _SSN.sub(SSN, s)
    s = _CARD.sub(CARD, s)
    s = _PHONE.sub(PHONE, s)
    s = _TRACKING.sub(NUMBER, s)
    s = _LONG_DIGITS.sub(NUMBER, s)
    s = _MONEY.sub(MONEY, s)
    s = _PO_BOX.sub(ADDRESS, s)
    s = _STREET.sub(ADDRESS, s)
    s = _ZIP_PLUS4.sub(ADDRESS, s)
    s = _STATE_ZIP.sub(ADDRESS, s)
    s = _NAME_AFTER.sub(lambda m: m.group(1) + NAME, s)
    s = _TOKENISH.sub(_tokens, s)
    if max_len is not None and len(s) > max_len:
        s = s[:max_len] + "..."
    return s


def looks_secret(text):
    """Typed text whose target field is unknown: could it be a password or code?

    A one-time code (4-8 digits alone), or one 'word' of 6-64 characters with
    no spaces that mixes character classes the way passwords do."""
    t = (text or "").strip()
    if not t:
        return False
    if re.fullmatch(r"\d{4,8}", t):
        return True
    if " " in t or len(t) < 6 or len(t) > 64:
        return False
    if re.fullmatch(r"[a-z]+", t) or re.fullmatch(r"[A-Z][a-z]+", t):
        return False                         # a plain word: a search term, a name
    if re.fullmatch(r"https?://\S+", t) or re.fullmatch(r"[\w.-]+\.[a-z]{2,}(/\S*)?", t):
        return False                         # a URL or host typed into an address field
    classes = sum(bool(re.search(p, t)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    return classes >= 3 or (classes == 2 and bool(re.search(r"\d", t)) and len(t) >= 8)


def scrub_typed(text, target=None, trusted=True):
    """Text an agent or helper typed. Returns (text_or_None, changed: bool).

    Withheld entirely (not even the length) when any of these holds:
      * the target is a sensitive field;
      * the text looks like a password or a code, whatever field it went into
        (a field's name can lie: a password box called "username");
      * the target is unknown or not trusted: it came from a page snapshot taken
        before the previous step, and was not clicked or focused just before.
    Otherwise emails, phones, money, addresses, names and tokens are masked."""
    if text is None:
        return None, False
    if target is not None and is_sensitive_field(target):
        return REDACTED, True
    if looks_secret(text):
        return REDACTED, True
    if target is None or not trusted:
        return REDACTED, True
    out = scrub_text(text, 2000)
    return out, out != text


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
SENSITIVE_PARAMS = re.compile(
    r"^(access_?token|id_?token|refresh_?token|token|auth|auth_?token|authorization|code|state|nonce|"
    r"session|session_?id|sessionid|sid|ssid|jsessionid|phpsessid|asp\.net_sessionid|key|api_?key|apikey|"
    r"secret|client_?secret|password|passwd|pwd|pass|otp|pin|ticket|signature|sig|hmac|hash|"
    r"x-amz-[a-z-]+|x-goog-[a-z-]+|awsaccesskeyid|credential|credentials|jwt|saml(request|response)|"
    r"relaystate|oauth_[a-z_]+|code_?verifier|code_?challenge|magic|magic_?link|login_?token|reset_?token|"
    r"verification_?token|confirm(ation)?_?token|invite_?token|email|e-mail|mail|phone|tel|mobile|ssn|"
    r"cc|card|cvv|cvc|account|acct|_ga|_gl|gclid|fbclid|msclkid|mc_eid|vero_id|wickedid|yclid|dclid|"
    r"cookie|csrf|_csrf|csrf_?token|xsrf|authenticity_token|u|uid|user_?id|userid)$", re.I)


def _scrub_param_value(k, v):
    if SENSITIVE_PARAMS.match(k or ""):
        return REDACTED
    if looks_random(v, 16) or _JWT.search(v) or _KNOWN_KEYS.search(v):
        return TOKEN
    return scrub_text(v)


def _scrub_query(q):
    if not q:
        return q
    out = []
    for part in q.split("&"):
        if "=" in part:
            k, _, v = part.partition("=")
            try:
                dv = parse_qsl(part, keep_blank_values=True)[0][1]
            except (IndexError, ValueError):
                dv = v
            nv = _scrub_param_value(k, dv)
            out.append(f"{k}={v if nv == dv else quote(nv, safe='[]')}")
        else:
            nv = scrub_text(part)
            out.append(TOKEN if looks_random(part, 16) else nv)
    return "&".join(out)


def scrub_url(url, max_len=500):
    """A URL with credentials, tokens, session ids and personal data taken out.

    Host and the shape of the path stay, because those are what a model learns from."""
    if not url:
        return url
    url = str(url)
    low = url[:12].lower()
    if low.startswith("data:"):
        return "data:" + REDACTED
    if low.startswith("javascript:"):
        return "javascript:" + scrub_js(url[11:])
    if low.startswith("blob:"):
        return "blob:" + REDACTED
    try:
        p = urlsplit(url)
    except ValueError:
        return scrub_text(url, max_len)
    netloc = p.netloc
    if "@" in netloc:
        netloc = REDACTED + "@" + netloc.rsplit("@", 1)[1]
    segs = []
    for seg in p.path.split("/"):
        if looks_random(seg, 20) or _JWT.search(seg) or _KNOWN_KEYS.search(seg):
            segs.append(TOKEN)
        else:
            segs.append(scrub_text(seg))
    path = "/".join(segs)
    query = _scrub_query(p.query)
    frag = p.fragment
    if frag:
        # OAuth implicit flow puts access_token in the fragment; SPA routes carry ?a=b there too
        if "=" in frag:
            head, sep, rest = frag.partition("?")
            if sep:
                frag = scrub_text(head) + "?" + _scrub_query(rest)
            else:
                frag = _scrub_query(frag)
        else:
            frag = TOKEN if looks_random(frag, 20) else scrub_text(frag)
    out = urlunsplit((p.scheme, netloc, path, query, frag))
    if len(out) > max_len:
        out = out[:max_len] + "..."
    return out


# ---------------------------------------------------------------------------
# JavaScript an agent ran
# ---------------------------------------------------------------------------
_JS_SENSITIVE = re.compile(
    r"password|passwd|pwd|passcode|cvv|cvc|card|otp|one-time|ssn|secret|token|cookie|localStorage|"
    r"sessionStorage|indexedDB|authorization|credential|api_?key|apikey|session|\.value\s*=|"
    r"insertText|setRangeText|execCommand|dispatchEvent|\.fill\(|\.type\(", re.I)
_JS_STRING = re.compile(r"`(?:\\.|[^`\\])*`|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.S)
# selectors and attribute names are what a model learns from; keep those literals
_JS_KEEP_LITERAL = re.compile(r"^['\"][\w\s#.\[\]=\-:>+~*^$|()'\",]*['\"]$")


_JS_SELECTOR_CALL = re.compile(
    r"(?:querySelector(?:All)?|closest|matches|getElementById|getElementsByClassName|getElementsByName|"
    r"getElementsByTagName|waitForSelector|\$\$?|\$x)\s*\(\s*$")


def scrub_js(code, max_len=2000, force_literals=True, sensitive=False):
    """JavaScript an agent ran (Runtime.evaluate / Runtime.callFunctionOn).

    Every string literal is removed, except the first argument of a selector call
    (querySelector('#login'), closest('form')...) when it is plainly CSS and does
    not look like a secret. el.setAttribute('value', 'Tr0ub4dor&3') loses both.
    With sensitive=True (the target is a sensitive field) selectors go too.
    force_literals=False keeps the old keyword-triggered behaviour for callers
    that scrub something other than code the agent ran."""
    if code is None:
        return None
    code = str(code)
    if len(code) > 20000:
        code = code[:20000]
    if force_literals or sensitive or _JS_SENSITIVE.search(code):
        def lit(m):
            s = m.group(0)
            inner = s[1:-1]
            if (not sensitive and s[0] != "`" and _JS_SELECTOR_CALL.search(code[max(0, m.start() - 40):m.start()])
                    and len(inner) <= 120 and not looks_secret(inner) and not re.search(r"@|\d{4,}", inner)
                    and (_looks_like_selector(inner) or re.fullmatch(r"[a-zA-Z][\w-]{0,40}", inner))):
                return s
            return '"' + REDACTED + '"'
        code = _JS_STRING.sub(lit, code)
    return scrub_text(code, max_len)


_SEL_PART = re.compile(
    r"^(?:[a-zA-Z][\w-]*|\*)?(?:[#.][A-Za-z_-][\w-]*|\[[\w-]+(?:[~|^$*]?=(?:\"[^\"]{0,60}\"|'[^']{0,60}'|[\w-]{1,60}))?\]"
    r"|::?[\w-]+(?:\([^)]{0,40}\))?)+$")


def _looks_like_selector(s):
    """'#login', 'input[name=email]', 'form .submit > button' -- CSS, not free text.

    Every part must be CSS syntax with at least one #id, .class, [attr] or :pseudo,
    so a bare word (which could be the secret itself) never counts."""
    parts = [p for p in re.split(r"\s*[\s>+~,]\s*", s.strip()) if p]
    if not parts:
        return False
    return all(_SEL_PART.match(p) or re.fullmatch(r"[a-zA-Z][\w-]*", p) for p in parts) and \
        any(_SEL_PART.match(p) for p in parts)


# ---------------------------------------------------------------------------
# hosts where nothing but the action and the element's role is recorded
# ---------------------------------------------------------------------------
# Mail, messages, banks, payments, government and health: the page itself is the
# private data. Extend with BROKER_RECORD_DENY_HOSTS (comma-separated host suffixes
# or substrings beginning with "*", e.g. "example.com,*credit").
DENY_HOSTS = (
    "mail.google.com", "inbox.google.com", "voice.google.com", "messages.google.com", "contacts.google.com",
    "drive.google.com", "docs.google.com", "photos.google.com", "calendar.google.com", "pay.google.com",
    "outlook.live.com", "outlook.office.com", "outlook.office365.com", "mail.yahoo.com", "mail.proton.me",
    "proton.me", "icloud.com", "fastmail.com", "mail.hostinger.com", "webmail.",
    "authorize.net", "paypal.com", "venmo.com", "cash.app", "stripe.com", "squareup.com", "wise.com",
    "chase.com", "wellsfargo.com", "bankofamerica.com", "capitalone.com", "americanexpress.com",
    "discover.com", "citi.com", "usbank.com", "schwab.com", "fidelity.com", "vanguard.com", "coinbase.com",
    "irs.gov", "ssa.gov", "cdtfa.ca.gov", "edd.ca.gov", "dmv.ca.gov", "healthcare.gov", "mychart",
    "web.whatsapp.com", "web.telegram.org", "messenger.com", "wechat.com", "web.wechat.com",
)
DENY_PATTERNS = ("bank", "credit", "pay.", "billing", "wallet", "login.", "accounts.", "signin.", "auth.")


def _host(url):
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


def host_denied(url, extra=None):
    """True when `url` is on a host where only the action and the role are recorded."""
    h = _host(url)
    if not h:
        return False
    for d in tuple(DENY_HOSTS) + tuple(extra or ()):
        d = d.strip().lower()
        if not d:
            continue
        if d.startswith("*"):
            if d[1:] in h:
                return True
        elif d.endswith("."):
            if h.startswith(d) or ("." + d) in h:
                return True
        elif h == d or h.endswith("." + d) or (d.isalpha() and d in h):
            return True
    return any(p in h for p in DENY_PATTERNS)


def host_allowed(url, allow):
    """True when `url`'s host is on the explicit allowlist (exact host or a subdomain of one)."""
    h = _host(url)
    return bool(h) and any(h == a or h.endswith("." + a) for a in (x.strip().lower() for x in allow or ()) if a)


# ---------------------------------------------------------------------------
# a whole record, as a last net
# ---------------------------------------------------------------------------
# Fields whose values the recorder itself generates (ids, enums, numbers). Everything
# else, at any depth, is run through scrub_text again.
TRUSTED_KEYS = frozenset({"type", "job", "tab", "seq", "ts", "t", "actor", "id", "node", "role", "tag",
                          "method", "action", "kind", "outcome_source", "schema", "box", "button",
                          "input_type", "focused", "started", "ended", "screenshot", "owner_kind"})


def deep_scrub(obj, key=None):
    if isinstance(obj, dict):
        return {k: deep_scrub(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deep_scrub(v, key) for v in obj]
    if isinstance(obj, str):
        if key in TRUSTED_KEYS and len(obj) <= 64 and re.fullmatch(r"[\w.:/\- +]*", obj):
            return obj
        if key == "url":
            return scrub_url(obj)
        return scrub_text(obj)
    return obj


# Header names that are never written, whatever their value.
SECRET_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "set-cookie",
                            "x-api-key", "x-auth-token", "x-csrf-token", "x-xsrf-token"})

# CDP domains whose params are never written at all (bodies, cookies, storage, credentials).
NEVER_PARAMS = ("Network.", "Fetch.", "Storage.", "DOMStorage.", "IndexedDB.", "CacheStorage.",
                "Security.", "WebAuthn.", "Audits.", "ServiceWorker.", "BackgroundService.",
                "Autofill.", "FedCm.")
