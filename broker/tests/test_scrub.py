"""scrub.py: nothing secret or personal survives into a trajectory. Adversarial on purpose."""
import json

import pytest

import scrub
import recorder


# ---- sensitive fields ---------------------------------------------------------
@pytest.mark.parametrize("field", [
    {"type": "password"},
    {"type": "PASSWORD"},
    {"type": "text", "ac": "current-password"},
    {"type": "text", "autocomplete": "username new-password"},
    {"type": "text", "ac": "one-time-code"},
    {"type": "tel", "ac": "cc-number"},
    {"type": "text", "ac": "cc-csc"},
    {"type": "text", "ac": "cc-exp-month"},
    {"type": "text", "ac": "bday"},
    {"type": "text", "fname": "pass"},
    {"type": "text", "fname": "user_pass"},
    {"type": "text", "fname": "pwd"},
    {"type": "text", "fid": "loginPassword"},
    {"type": "text", "label": "Card number"},
    {"type": "text", "label": "CVC"},
    {"type": "text", "placeholder": "CVV"},
    {"type": "text", "label": "Security code"},
    {"type": "text", "label": "Expiration date (MM/YY)"},
    {"type": "text", "label": "Social Security Number"},
    {"type": "text", "fname": "ssn"},
    {"type": "text", "label": "Enter the 6-digit verification code"},
    {"type": "text", "fname": "otp"},
    {"type": "text", "fname": "totp_code"},
    {"type": "text", "label": "One-time passcode"},
    {"type": "text", "label": "PIN"},
    {"type": "text", "fname": "api_key"},
    {"type": "text", "label": "Secret"},
    {"type": "text", "fname": "access-token"},
    {"type": "text", "label": "Routing number"},
    {"type": "text", "label": "Account number"},
    {"type": "text", "label": "IBAN"},
    {"type": "text", "label": "Recovery phrase"},
    {"type": "text", "label": "Date of birth"},
    {"type": "text", "fname": "dob"},
    {"type": "text", "label": "Passport number"},
    {"type": "text", "aria": "Answer to your security question"},
    {"type": "text", "fname": "cc_num"},
    {"type": "text", "sensitive": True},
])
def test_sensitive_fields(field):
    assert scrub.is_sensitive_field(field)


@pytest.mark.parametrize("field", [
    {"type": "text", "label": "Search"},
    {"type": "email", "label": "Email"},
    {"type": "text", "label": "First name"},
    {"type": "text", "label": "Company"},
    {"type": "text", "fname": "q"},
    {"type": "text", "label": "Spinner speed"},     # "pin" inside a word
    {"type": "text", "label": "Passenger name"},     # "pass" inside a word
    {"type": "text", "label": "Shipping address"},
    None,
    {},
])
def test_ordinary_fields(field):
    assert not scrub.is_sensitive_field(field)


# ---- typed text -----------------------------------------------------------------
def test_typed_into_sensitive_field_is_gone_entirely():
    text, red = scrub.scrub_typed("hunter2", {"type": "password"})
    assert text == scrub.REDACTED and red
    text, _ = scrub.scrub_typed("4111 1111 1111 1111", {"label": "Card number"})
    assert text == scrub.REDACTED


@pytest.mark.parametrize("secret", ["Hunter2!x", "P@ssw0rd", "correcthorse9battery", "482913", "0042", "aB3$eF6&"])
def test_secret_looking_text_into_unknown_field_is_redacted(secret):
    text, red = scrub.scrub_typed(secret, None)
    assert text == scrub.REDACTED and red


@pytest.mark.parametrize("plain", ["vaporizer coils", "hello", "Matt", "https://ineedhemp.com", "ineedhemp.com"])
def test_plain_text_into_unknown_field_is_kept(plain):
    assert scrub.scrub_typed(plain, None) == (plain, False)


def test_typed_text_masks_email_and_phone_in_ordinary_field():
    text, red = scrub.scrub_typed("reach me at matt@example.com or (707) 555-0134 thanks",
                                  {"type": "text", "label": "Message"})
    assert "matt@example.com" not in text and "555-0134" not in text
    assert scrub.EMAIL in text and scrub.PHONE in text and red


def test_bare_email_typed_into_email_field_is_masked():
    text, _ = scrub.scrub_typed("someone.else+tag@sub.example.co.uk", {"type": "email", "label": "Email"})
    assert text == scrub.EMAIL


# ---- free text ------------------------------------------------------------------
@pytest.mark.parametrize("s,gone", [
    ("Authorization: Bearer abc.def.ghi123456", "abc.def.ghi123456"),
    ('{"authorization": "Basic dXNlcjpwYXNz"}', "dXNlcjpwYXNz"),
    ("Cookie: sid=31d4d96e407aad42; theme=dark", "31d4d96e407aad42"),
    ("set-cookie: session=abcdef0123456789abcdef; HttpOnly", "abcdef0123456789abcdef"),
    ("x-api-key: k_live_2b4c6d8e0f", "k_live_2b4c6d8e0f"),
    ("token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.sflKxwRJSMeKKF2QT4fwpM", "sflKxwRJSMeKKF2QT4fwpM"),
    ("key sk-proj-AbCdEfGhIjKlMnOpQrStUv123", "AbCdEfGhIjKlMnOpQrStUv123"),
    ("ghp_1234567890abcdefghijABCDEFGHIJ", "ghp_1234567890abcdefghij"),
    ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("AIzaSyD-1234567890abcdefghijklmnopqrstu", "AIzaSyD-1234567890"),
    ("xoxb-123456789012-abcdefghijkl", "xoxb-123456789012"),
    ("password=hunter2", "hunter2"),
    ('"pwd": "s3cr3t"', "s3cr3t"),
    ("my api_key: 9f8e7d6c", "9f8e7d6c"),
    ("session_id=Zx81Qp0LmN", "Zx81Qp0LmN"),
    ("card 4111-1111-1111-1111 exp", "4111-1111-1111-1111"),
    ("card 4111111111111111", "4111111111111111"),
    ("amex 3782 822463 10005", "3782 822463 10005"),
    ("ssn 123-45-6789", "123-45-6789"),
    ("call +1 707 555 0134", "555 0134"),
    ("call 707.555.0134", "707.555.0134"),
    ("call 7075550134", "7075550134"),
    ("intl +447911123456", "+447911123456"),
    ("mail Matt.M+x@Example.COM now", "Matt.M+x@Example.COM"),
    ("mail matt%40example.com", "matt%40example.com"),
    ("sid 3f9a1c2b7d4e8f6a0b1c2d3e4f5a6b7c", "3f9a1c2b7d4e8f6a0b1c2d3e4f5a6b7c"),
    ("token Q2hhbmdlTWUhIFRoaXNJc0FTZWNyZXQx", "Q2hhbmdlTWUhIFRoaXNJc0FTZWNyZXQx"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA"),
])
def test_scrub_text_removes(s, gone):
    out = scrub.scrub_text(s)
    assert gone not in out, out


@pytest.mark.parametrize("s", [
    "Add to cart", "Ruby Twist ceramic heater - $129.99", "Order placed on 2026-10-31 at 3:15 PM",
    "how-to-clean-maintain-your-vaporizer-make-your-heater-last-a-year", "Sign in", "Page 2 of 14",
])
def test_scrub_text_keeps_ordinary_text(s):
    assert scrub.scrub_text(s) == s


def test_scrub_text_is_idempotent():
    s = "Bearer abcdefghijkl matt@example.com 4111111111111111 password=x"
    once = scrub.scrub_text(s)
    assert scrub.scrub_text(once) == once


# ---- URLs -----------------------------------------------------------------------
@pytest.mark.parametrize("url,gone", [
    ("https://user:hunter2@example.com/x", "hunter2"),
    ("https://example.com/cb?code=4/0AX4XfWh&state=xyz123", "4/0AX4XfWh"),
    ("https://example.com/cb?code=abc&state=xyz123", "xyz123"),
    ("https://example.com/#access_token=ya29.a0AfH6SMBx&token_type=Bearer", "ya29.a0AfH6SMBx"),
    ("https://example.com/app#/route?token=abcd1234efgh", "abcd1234efgh"),
    ("https://example.com/reset/7f3k9QpL2mXz8RtY4wNb6VcD", "7f3k9QpL2mXz8RtY4wNb6VcD"),
    ("https://example.com/s?sessionid=1a2b3c4d", "1a2b3c4d"),
    ("https://example.com/s?PHPSESSID=deadbeefcafe", "deadbeefcafe"),
    ("https://example.com/s?ref=Zm9vYmFyYmF6cXV4MTIzNDU2", "Zm9vYmFyYmF6cXV4MTIzNDU2"),
    ("https://example.com/s?email=matt%40example.com", "matt"),
    ("https://example.com/s?to=matt@example.com", "matt@example.com"),
    ("https://example.com/s?phone=7075550134", "7075550134"),
    ("https://bucket.s3.amazonaws.com/f?X-Amz-Signature=abc123def&X-Amz-Credential=AKIA", "abc123def"),
    ("https://example.com/u/matt@example.com/profile", "matt@example.com"),
    ("https://example.com/verify/eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcDEF123", "eyJhbGciOiJIUzI1NiJ9"),
    ("https://example.com/?api_key=k1", "k1"),
    ("https://example.com/?gclid=EAIaIQobChMI", "EAIaIQobChMI"),
    ("data:text/html,<input value=secret>", "secret"),
    ("javascript:document.querySelector('#pw').value='hunter2'", "hunter2"),
])
def test_scrub_url_removes(url, gone):
    out = scrub.scrub_url(url)
    assert gone not in out, out


@pytest.mark.parametrize("url", [
    "https://ineedhemp.com/product/ruby-twist/",
    "https://www.google.com/search?q=vaporizer+coils",
    "https://ineedhemp.com/wp-admin/post.php?post=206256&action=edit",
    "about:blank",
])
def test_scrub_url_keeps_ordinary_urls(url):
    assert scrub.scrub_url(url) == url


def test_scrub_url_keeps_host_and_path_shape():
    out = scrub.scrub_url("https://accounts.example.com/o/oauth2/auth?client_id=abc&code=SECRET123&redirect_uri=x")
    assert out.startswith("https://accounts.example.com/o/oauth2/auth?")
    assert "SECRET123" not in out and "client_id=abc" in out


# ---- JavaScript -----------------------------------------------------------------
@pytest.mark.parametrize("code,gone", [
    ("document.querySelector('#password').value = 'hunter2'", "hunter2"),
    ("el.value = \"P@ssw0rd!\"", "P@ssw0rd!"),
    ("document.execCommand('insertText', false, 'my secret text')", "my secret text"),
    ("localStorage.setItem('auth', 'abc123')", "abc123"),
    ("document.cookie = 'sid=xyz789'", "xyz789"),
    ("fetch('/api', {headers: {Authorization: `Bearer ${'tok_123456'}`}})", "tok_123456"),
    ("x.value=`multi\nline secret`", "line secret"),
    ("const t = 'hunter2'; document.getElementById('pw').value = t", "hunter2"),
])
def test_scrub_js_removes(code, gone):
    assert gone not in scrub.scrub_js(code)


def test_scrub_js_keeps_selectors_and_harmless_code():
    code = "document.querySelector('button[type=submit]').click()"
    assert scrub.scrub_js(code) == code
    code = "document.querySelector('#email').value = 'x'"
    out = scrub.scrub_js(code)
    assert "'#email'" in out
    assert scrub.scrub_js("window.scrollBy(0, 400)") == "window.scrollBy(0, 400)"


def test_scrub_js_forced_redacts_every_literal():
    out = scrub.scrub_js("el.focus(); type('abc')", force_literals=True)
    assert "abc" not in out


# ---- whole records ------------------------------------------------------------
def test_deep_scrub_catches_anything_left_anywhere():
    rec = {"type": "step", "job": "abc123", "action": {"type": "type", "text": "matt@example.com"},
           "observation": {"elements": [{"name": "Signed in as matt@example.com", "box": [1, 2, 3, 4]}],
                           "url": "https://x.com/?token=abcdef123456"},
           "nested": [["Bearer abcdefghijklmnop"]]}
    out = json.dumps(scrub.deep_scrub(rec))
    for gone in ("matt@example.com", "abcdef123456", "abcdefghijklmnop"):
        assert gone not in out
    assert '"job": "abc123"' in out


def test_safe_action_never_copies_unknown_params():
    a, _ = recorder.semantic("Input.insertText", {"text": "hunter2!A", "extra": "SECRETPARAM"})
    a["method"] = "Input.insertText"
    out = recorder.safe_action(a, {"type": "password"})
    assert out["text"] == scrub.REDACTED
    assert "SECRETPARAM" not in json.dumps(out) and "hunter2" not in json.dumps(out)


def test_upload_keeps_only_file_names():
    a, _ = recorder.semantic("DOM.setFileInputFiles",
                             {"files": ["/Users/dtribe/Private/taxes-2025.pdf", "/tmp/a.png"], "nodeId": 3})
    a["method"] = "DOM.setFileInputFiles"
    out = recorder.safe_action(a, None)
    assert out["files"] == ["taxes-2025.pdf", "a.png"] and "/Users" not in json.dumps(out)


def test_js_into_sensitive_field_redacts_every_literal():
    a, _ = recorder.semantic("Runtime.evaluate", {"expression": "type('#login', 'plainword')"})
    a["method"] = "Runtime.evaluate"
    out = recorder.safe_action(a, {"type": "password"})
    assert "plainword" not in out["code"]


def test_element_view_never_carries_a_value_or_raw_attributes():
    e = {"id": "e3", "tag": "input", "role": "textbox", "name": "Password", "box": [1, 2, 3, 4],
         "field": True, "type": "password", "fname": "pw", "fid": "pwid", "ac": "current-password",
         "filled": True, "value": "hunter2"}
    v = recorder.element_view(e)
    assert v["sensitive"] is True and v["filled"] is True
    s = json.dumps(v)
    assert "hunter2" not in s and "pwid" not in s and "current-password" not in s
