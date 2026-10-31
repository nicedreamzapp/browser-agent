---
title: Broker Recorder trajectory schema
description: >-
  The JSONL format the browser-broker recorder writes for every browser job:
  job header, steps with semantic actions, target elements and page snapshots,
  and the end record. Plus what is scrubbed and how to export SFT data.
---

# Broker Recorder: trajectory schema

The broker sees every CDP command every agent sends, in a real logged-in browser, and
it knows when the human stepped in. The recorder turns that into training data: one
JSONL file per job, each line a record, built to fine-tune small vision-language models
(LFM2-VL / LFM3-VL and the like) to drive a browser.

It is **off** unless you turn it on, and it is built so that turning it on changes
nothing about the traffic itself.

## Turning it on

| How | Records |
|---|---|
| `BROKER_RECORD=1` in the broker's environment (restart the broker) | every job |
| `POST /lease` with `"record": true` | that lease only |
| `X-Broker-Record: 1` header on a CDP websocket (`/devtools/browser/...` or `/devtools/page/...`) | that connection only |

Optional:

| Setting | Effect |
|---|---|
| `BROKER_RECORD_DIR` | output directory, default `~/.local/broker-recordings` |
| `BROKER_RECORD_SCREENSHOTS=1` | a masked half-scale PNG before each step |
| `BROKER_RECORD_QUEUE` | events held before dropping (default 5000) |

Give the job a task, so the trajectory says what it was for:

- `POST /lease {"owner": "...", "task": "Find the price of the Ruby Twist", "record": true}`
  (falls back to `purpose`), or
- `X-Broker-Task: <url-encoded text>` on the websocket, or
- `POST /record {"lease_id" | "job": ..., "task": "...", "outcome": "...", "note": "..."}` at any time.

Say how it went when you finish: `POST /release {"lease_id": ..., "outcome": "success"}`
(or `failure`, `partial`, `abandoned`, or any short text), or `POST /record`.

## How it stays out of the way

- The proxy calls the recorder through a few methods that do a set lookup and a
  `queue.put_nowait()`. Nothing is parsed, written or awaited on the proxy's event loop.
- A full queue drops the event and counts it (`dropped_events` in the end record). It never waits.
- Snapshots, screenshots, scrubbing and file writes run on one daemon thread with its own
  event loop and its own page socket to the browser (read-only `Runtime.evaluate` and
  `Page.captureScreenshot`; no input, no navigation, no focus).
- The page snapshot for a step is the newest one taken after the previous step settled
  (0.6 s, or 1.5 s after a click, Enter or navigation). The step is never held back to take a
  fresh one. If the agent moved faster than that, the step says `"observation_stale": true`.
- With recording off, the proxy holds `REC = None` and the CDP path is the same code as before.
  The test suite checks that traffic is byte-identical with recording off and on.

## Files

```
~/.local/broker-recordings/            0700
  2026-10-31/                          0700, the day the job started
    3f9a1c2b7d4e.jsonl                 0600, one job (REST lease id, ws:<id>, or pool-<tab>)
    3f9a1c2b7d4e/                      0700, screenshots for that job, when enabled
      7.png                            0600, the page before step 7
```

A job's file is created at its first step. Lines are appended as the job runs, so a crash
leaves everything up to that point.

## Records

Every line is one JSON object with a `type`.

### `job` (first line)

```json
{"type": "job", "schema": 1, "job": "3f9a1c2b7d4e", "owner": "price-check", "owner_kind": "rest",
 "task": "Find the price of the Ruby Twist", "started": "2026-10-31T09:12:03.120", "t": 1793452323.12,
 "recorder": {"screenshots": false}}
```

| Field | Meaning |
|---|---|
| `job` | REST lease id; `ws_<id>` for a browser-level CDP client; `pool-<tab>` for a legacy `/json` tab |
| `owner_kind` | `rest`, `ws` or `pool` |
| `task` | what the client said the job was for (scrubbed), or `null` |
| `t` | Unix seconds; `started` is the same moment in local ISO time |

### `step`

```json
{"type": "step", "schema": 1, "job": "3f9a1c2b7d4e", "seq": 14, "ts": "2026-10-31T09:12:09.402",
 "t": 1793452329.402, "tab": "C1C5C79E6967", "actor": "agent",
 "url": "https://shop.example.com/login?session=[REDACTED]", "title": "Sign in",
 "action": {"type": "type", "method": "Input.insertText", "text": "[REDACTED]", "via": "insertText",
            "redacted": true},
 "target": {"node": "e2", "role": "textbox", "tag": "input", "name": "Password", "box": [100, 160, 300, 40],
            "input_type": "password", "sensitive": true, "filled": false},
 "observation": {"url": "...", "title": "Sign in", "viewport": [1280, 800], "scroll": [0, 0], "focused": "e2",
                 "elements": [{"node": "e0", "role": "heading", "tag": "h1", "name": "Sign in", "box": [100, 40, 400, 40]},
                              {"node": "e1", "role": "textbox", "tag": "input", "name": "Email", "box": [100, 100, 300, 40],
                               "input_type": "email", "sensitive": false, "filled": true}]},
 "observation_age": 0.84, "observation_stale": false,
 "screenshot": null,
 "result": {"ok": true}}
```

| Field | Meaning |
|---|---|
| `seq` | order within the broker run; sort by it (lines are written as replies arrive) |
| `tab` | first 12 characters of the target id |
| `actor` | `agent` (a CDP command) or `human` (real input in a broker tab, from the broker's trusted-input listener; agent echoes are already discounted) |
| `url`, `title` | from the observation, scrubbed |
| `action` | see below |
| `target` | the element acted on, from the observation: hit-tested at the click point, or the focused element (or the field just clicked) for typing and keys. `null` when unknown |
| `observation` | compact page snapshot before the step, or `{"same_as": <seq>}` when identical to an earlier step's, or `null` if none was taken yet |
| `observation_age` | seconds between the snapshot and the step |
| `observation_stale` | the snapshot was taken before the previous step finished |
| `screenshot` | path relative to the recordings root, or `null` |
| `result` | `{"ok": true}`; `{"ok": false, "error": "..."}`; with `"refused_by_broker": true` when the broker refused it (the human was working in the tab, etc.); `{"ok": null, "note": "no reply"}` after 15 s without a reply |

#### Actions

Only these CDP commands become steps. Everything else (`Network.*`, `Storage.*`, `DOM.getDocument`,
`Emulation.*`, ...) is never written at all, not even its name.

| `action.type` | From | Fields |
|---|---|---|
| `navigate` | `Page.navigate` | `url` |
| `reload` | `Page.reload` | |
| `history` | `Page.navigateToHistoryEntry` | `entry` |
| `click` | `Input.dispatchMouseEvent` mousePressed, `Input.synthesizeTapGesture`, `Input.dispatchTouchEvent` touchStart | `x`, `y` (CSS px, viewport), `button`, `count` |
| `scroll` | `Input.dispatchMouseEvent` mouseWheel, `Input.synthesizeScrollGesture` | `x`, `y`, `dx`, `dy` |
| `type` | `Input.insertText`, or `Input.dispatchKeyEvent` with text (consecutive keystrokes into the same field are merged into one step) | `text`, `via` (`insertText`/`keys`), `redacted` or `masked` |
| `key` | `Input.dispatchKeyEvent` for Enter, Tab, arrows, Backspace, shortcuts | `key`, `modifiers` |
| `upload` | `DOM.setFileInputFiles` | `files` (base names only), `count` |
| `focus` | `DOM.focus` | |
| `set_value` | `DOM.setAttributeValue`, `DOM.setNodeValue` | `attribute`, `text` |
| `js` / `select` | `Runtime.evaluate`, `Runtime.callFunctionOn` (`select` when it sets an option) | `code` (scrubbed, max 2000 chars), `args` (count only; argument values are never written) |
| `open_tab` / `close_tab` | `Target.createTarget` / `Target.closeTarget` | `url` |

Human steps carry `{"type": click | key | type | paste | scroll, "count": n, "event": "<dom event>"}`
and never any text.

#### Elements

`{"node", "role", "tag", "name", "box": [x, y, w, h]}`, and for form fields `input_type`,
`sensitive`, and `filled` or `checked`. `name` is the accessible name (aria-label, label,
placeholder, alt, title or text), scrubbed, at most 80 characters. Up to 150 visible
interactive elements, headings and labelled images per snapshot. **A field's value is never
read out of the page**, only whether it is filled.

### `end` (last line)

```json
{"type": "end", "job": "3f9a1c2b7d4e", "ended": "2026-10-31T09:13:40.002", "outcome": "success",
 "outcome_source": "client", "end_reason": "released", "task": "Find the price of the Ruby Twist",
 "note": null, "steps": 23, "redacted_steps": 2, "dropped_events": 0}
```

`outcome` is what the client said, or `unknown` (`outcome_source: "none"`). `end_reason` is why the
job ended: `released`, `lease expired`, `client disconnected`, `broker stopped`, ...

## What is scrubbed

All of it happens before anything is written (`scrub.py`, tested in `tests/test_scrub.py`).
Records are built field by field from the table above; raw CDP params are never copied.
Then every string in the record goes through the scrubber again.

- **Sensitive fields.** Password inputs; autocomplete `current-password`, `new-password`,
  `one-time-code`, `cc-*`, `bday*`; names, ids, labels or placeholders that say password,
  passcode, PIN, OTP, 2FA, verification / security code, card, CVC / CVV, expiry, SSN, tax id,
  routing / account number, IBAN, secret, token, API key, recovery / seed phrase, date of birth,
  passport, licence number. Text typed, inserted or set into one is replaced by `[REDACTED]`,
  length included. JavaScript run while one is the target has every string literal removed.
- **Unknown fields.** Typed text whose target is unknown is withheld when it looks like a
  password (one word, 6-64 characters, mixed character classes) or a code (4-8 digits alone).
- **Free text everywhere** (typed text, element names, titles, tasks, errors, code): emails,
  phone numbers, 13-19 digit card-like numbers, SSNs, JWTs, bearer / basic credentials,
  `Authorization`, `Cookie`, `Set-Cookie` and API-key headers, `password=` / `token:` style
  pairs, private keys, known key formats (OpenAI, Anthropic, Stripe, GitHub, Slack, AWS, Google,
  Hugging Face, ...), and long random-looking strings (session ids, tokens) become placeholders:
  `[EMAIL]`, `[PHONE]`, `[CARD]`, `[SSN]`, `[TOKEN]`, `[REDACTED]`.
- **URLs.** `user:password@` is dropped; query and fragment parameters named like tokens,
  codes, state, sessions, keys, signatures, emails, phones or click ids are `[REDACTED]`;
  random-looking values and path segments are `[TOKEN]`; `data:` and `blob:` URLs are dropped.
- **JavaScript.** When code touches `.value`, `insertText`, cookies, storage, credentials or a
  sensitive name, every string literal that is not plainly a CSS selector is removed.
- **Never written:** cookies, request / response headers or bodies, anything from the
  `Network`, `Fetch`, `Storage`, `DOMStorage`, `IndexedDB`, `CacheStorage`, `Security`,
  `WebAuthn` and `Autofill` domains, file paths (base names only), `callFunctionOn` arguments,
  any command result beyond ok / error.
- **Screenshots** (only with `BROKER_RECORD_SCREENSHOTS=1`): every filled field, every password
  field and every iframe (payment and captcha frames) is painted black before the PNG is written.
  The masks are read again after the capture; if they moved, the screenshot is discarded. A PNG
  the masker cannot rewrite is discarded too, never written unmasked.

### Known gaps

- Page text in **screenshots** is not scrubbed: an inbox or an account page shows names and
  addresses. Screenshots are off by default for that reason.
- Element names are scrubbed for emails, phones and tokens, but other personal text (a person's
  name in a link, a message subject) can remain.
- Elements inside iframes and shadow roots are not in the snapshot.
- A field the page does not label as sensitive in any way (a password box named `field_7`
  with no label) relies on the unknown-field heuristic.
- Recordings are not pruned; delete old days yourself.

## Exporting training data

`record_export.py` turns recordings into SFT JSONL: one sample per successful agent step.

```bash
python3 record_export.py --stats                         # what is there
python3 record_export.py -o train.jsonl                  # OpenAI-style tool calls (TRL, most trainers)
python3 record_export.py --format lfm -o train.jsonl     # LFM2's own tool-call tokens, inline
python3 record_export.py --images -o train.jsonl         # screenshot as an image part (vision models)
```

Each sample is `system` (the agent's role and tools) + `user` (task, the last six actions, and
the page as `[e3] button "Sign in" @100,220,120,40` lines) → `assistant` (one tool call such as
`click(element="e3")` or `type(element="e1", text="ruby twist")`).

Left out by default: human steps (they become "(the human: click)" lines in the history),
failed or refused steps, steps with no or a stale observation (`--allow-stale`), steps whose
text was withheld (`--keep-redacted`), jobs without a task (`--allow-no-task`) and jobs whose
outcome is a failure (`--include-failed`). `--only-success` keeps only jobs marked `success`;
`--drop-js` leaves out `run_js`. Output files are written `0600`, and every sample goes through
the scrubber once more.
