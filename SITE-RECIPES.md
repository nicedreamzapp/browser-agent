# Site recipes: what real sites need from an agent

Hard-won notes from driving real, logged-in sites through the broker. Every entry here
cost a failed run first. Add to this file the moment you learn something; that is the
rule, so the next agent doesn't pay for the same lesson.

## Driving any tab

- **Click like a person: `bb-click <owner> "<text or selector>"`** (or `tab.click_trusted()`
  in `broker_client`). It screenshots, finds the element's box, and sends a trusted
  `Input.dispatchMouseEvent` press and release at its center, then screenshots again and
  prints what changed (URL, title, DOM, new tab, HTTP errors). Trusted mouse clicks DO land
  in hidden tabs (tested 2026-10-31: `event.isTrusted` true in a hidden lease); only the
  reply to `mouseMoved` lags about 5 s there, so the client sends it without waiting.
  Many sites ignore `element.click()` (`isTrusted` false) or need a user gesture to open a
  popup. `tab.click()` tries the script click first and retries trusted when nothing moves
  in 3 s, logging a line to `broker/click.log`.
- **Typing in hidden tabs:** `Input.insertText` often goes nowhere there. Set `.value` and
  fire `input`/`change` events for fields. On a tab the person is looking at, real input
  (`Input.insertText`) is the better choice, because many forms ignore values set from script.
- **Stuck in a hidden tab (blank page, 401/403, Sign in link) while his own tab is signed
  in:** `bb-click --check <owner>` says so plainly. Redo that one step visibly with
  `bb-click --visible <owner> "<button>"`, allowed only with `--matt-ok` (he said take over)
  or after he has been idle 10+ minutes; otherwise ask him in one line. Close the visible
  tab with `bb-close <owner>-visible`.
- **Look first, then fill, then read it back.** After filling, wait a couple of seconds
  and read every field again. Some forms re-render and silently wipe what you typed.
  Hand a page to the human only after the values have stuck.
- **Show a page once, then fill it.** Showing a page that is already open must never
  reload it. Fill after showing, never before, or the show wipes the work.
- **File uploads are the agent's own input.** `DOM.setFileInputFiles` fires trusted
  `change`/`input` events. The broker counts them as agent input, not human activity
  (fixed 2026-10-23).
- **Compare text ignoring whitespace.** Rich editors (Quill and similar) drop paragraph
  breaks from `innerText`, so a strict comparison fails on correct text.
- **A tab waiting on the human is his until he says done.** Authorize, login, captcha
  and "copy this code" pages stay open until he says he's finished and the result is
  verified. "Close tabs when done" means when *his* part is done, not the agent's step.
  On a timeout, extend the wait; never close and reopen the page (2026-10-24: an
  authorize tab was closed three times while he was on it).
- **Close and navigate only through the broker (`bb-close`).** Raw CDP calls
  (`/json/close/<id>`, `Target.closeTarget`, `Page.navigate` on the real browser port)
  skip the broker's human-is-working check and will close a tab out from under him.
  If a page looks stale, ask before closing it.
- **Never poll the broker's `/json` endpoints in a loop.** On the broker's proxy port,
  each `/json/list` call can create a hidden pool tab; a once-a-second watcher flooded
  the browser with about 30 blank tabs (2026-10-24). Use `bb-peek` or the broker's
  `/status` instead.

## Reading a site: first choice, then fallback

Sites change and tools break; that is normal, not an emergency. So every read gets an
ordered list of methods, tried in turn until one works. When a method breaks, move it
down the list or swap it out; the caller's command never changes. (Pattern borrowed from
[Agent-Reach](https://github.com/Panniantong/Agent-Reach).)

The default order, cheapest and sturdiest first:

1. **The site's own API or a local CLI** (`gh` for GitHub, WP-CLI/REST for WordPress,
   `yt-dlp` for video info and subtitles). No tab, no rendering.
2. **`fetch()` inside a logged-in tab.** Run it in the page's JavaScript context so it
   carries the session cookies, against the same JSON endpoints the site's own UI calls.
   Structured data, no DOM parsing.
3. **Read the rendered DOM** in a hidden broker lease. Slowest and most fragile; it
   breaks when the markup changes.
4. **Show the page to the human** only when everything above failed, and say which steps
   were tried.

Rules for the list:

- **Record the order per site** in that site's section of this file, with the date each
  method was last seen working. A method that failed twice in a row moves down.
- **Fall through on a real failure only:** an error, an empty result, a login wall. A
  slow answer is not a failure.
- **Report which method answered,** so a silent fallback can't hide a broken first choice.
- **Our own stack only.** No outside paid readers or proxies (Jina, Exa, scraping
  services) in the list; if a site needs one, write down why and how we would replace it.

## Posting

- **LinkedIn composer:** attach the image first, then type. As soon as the text holds a
  link, LinkedIn swaps the media toolbar for a link preview and the Add media button
  disappears. Type paragraphs with `Input.insertText` plus two Enter keys between them.
  Filling the editor with `\n\n` renders as huge gaps.

- **LinkedIn composer from a hidden lease (verified 2026-10-23, new TipTap/ProseMirror
  editor):** open it by URL, `https://www.linkedin.com/feed/?shareActive=true`. Mouse
  clicks and Enter key events did nothing there. Buttons: `Runtime.evaluate` with
  `userGesture: true` and `button.click()` ("Media", "Upload from computer", "Next", "Post").
  Text: `editor.focus()`, select its contents with a DOM Range, `Input.insertText` per
  line, and for each line break dispatch a synthetic
  `KeyboardEvent('keydown', {key:'Enter', bubbles:true})` on the editor (blank line = two).
  Read back exactly with `[...editor.children].map(p => p.textContent).join("\n")`.
  Image: `Page.setInterceptFileChooserDialog` never fired; patch
  `HTMLInputElement.prototype.click`/`showPicker` to stash the file input in `window.__fi`,
  click "Upload from computer", then `DOM.setFileInputFiles` on that objectId, then "Next".
  In this editor, text first then image worked fine (the links did not hide Media).
  After Post, the toast's "View post" link holds `/feed/update/urn:li:share:<id>/`.
  Re-verified 2026-10-29: clicking "Media" now opens the file picker directly (no
  "Upload from computer" step), so the same `click` patch catches the input on the Media
  click; after `DOM.setFileInputFiles`, "Next" returns to the composer. Image first, then a
  text holding a link, kept the uploaded image (no link card).
- **LinkedIn tab left open after the post (2026-10-29):** `/release` answered "left_open:
  the human is working in it" and the leased tab was relabeled the human's own, though
  nobody touched it (the Post submit fires trusted events). Always check Brave's real tab
  list afterwards and close that tab by target id with `bb-close --matt-asked`.

- **X (Twitter) composer:** Draft.js ignores `Input.insertText` AND `document.execCommand`
  in a background tab, so the composer stays empty. Show the composer as a visible tab
  (`bb-show https://x.com/compose/post`, ideally on a machine nobody is using), then type
  with `Input.insertText`, attach with `DOM.setFileInputFiles` on
  `input[data-testid="fileInput"]`, and click `tweetButton`. Check the attachment `img`
  appears before you post.

- **X text posts and replies, no typing (2026-10-25):** skip the composer entirely. In a hidden
  lease, call `Emulation.setFocusEmulationEnabled` (enabled: true), then open
  `https://x.com/intent/post?in_reply_to=<status id>&text=<url-encoded text>` (drop
  `in_reply_to` for a new post). X fills the composer itself, so Draft.js never sees a
  keystroke. Read the textarea back, check `tweetButton` is enabled, click it, then confirm
  the post on `/<handle>/with_replies`. Images still need the visible composer above.
  The profile timeline can lag several minutes after a post, so it is NOT proof of failure. Confirm with
  `x.com/search?q=from%3A<handle>%20%22<phrase>%22&f=live` instead. If you retry anyway, a CreateTweet
  reply with error 187 "Status is a duplicate" means the first try already posted (2026-10-25).

- **Reddit comments:** skip the composer. In a logged-in tab (a background tab is fine),
  read `modhash` from `/api/me.json`, then POST `/api/comment` (`api_type=json`,
  `thing_id=t3_<id>`, `text`) with the `X-Modhash` header. Confirm it worked by reading
  `/user/<name>/comments.json`. Reddit's site-wide search ORs your words, so search per
  subreddit (`/r/<sub>/search.json?restrict_sr=1`) with exact phrases.

## Privacy opt-out forms

- **Same-vendor families:** TruePeopleSearch, CyberBackgroundChecks,
  SmartBackgroundChecks, Advanced Background Checks, FamilyTreeNow and USPhonebook share
  one flow. Step 1 is name + email + captcha, and the site emails a link. Step 2 is a
  form at that link (DOB, phone, address) + captcha. Step 1 alone removes nothing;
  you're done when the "Privacy Request Processed" email arrives.
- **BeenVerified family** (also NeighborWho, PeopleSmart): picking "Delete" without an
  account flips the request back to "Do Not Sell" and clears the account answer. For
  people without an account, "Do Not Sell" plus "No, I do not" is the path that works.
  One request per email address.
- **Acxiom:** the US form lives in a cross-origin iframe built with ASP.NET UpdatePanels
  and a SlimSelect multi-picker. Select segments through the page's own widget API in
  the frame's main world (`optoutChoicesDropdown.setSelected([...])`). Every name,
  phone, email and address needs its own **Add** click before Submit, or the server
  rejects the form as empty. A confirmation email link follows, with a captcha.
- **Whitepages:** paste the profile URL, confirm the person, choose a reason, then a
  robot phone call reads you a code. A human has to answer the call.
- **Captchas are the human's job.** Fill everything, verify it stuck, then hand over
  with one line: "do the robot check and click X".

## Apple developer sites

- **Feedback Assistant / developer.apple.com:** when the session is cold, a hidden tab
  lands on `idmsa.apple.com/IDMSWebAuth/signin` with the form inside a cross-origin
  iframe, so `document.body.innerText` reads empty. Treat an empty body on idmsa as
  "signed out": stop and ask, never sign in. If the person signs in while the tab is
  open, the broker marks it as his and `/release` leaves it open; the session cookie
  then carries to new tabs, so re-run from a fresh lease only after he says go.
- **Feedback Assistant form (signed in, hidden tab works):** `/new` lists areas as
  buttons, and clicking one creates a draft right away. Delete any draft you open by
  mistake. API and entitlement reports go under "Developer Technologies & SDKs", which
  has platform, technology (e.g. App Intents Framework) and type selects. Setting the
  values natively and firing `input`/`change` sticks. Submit opens a "Missing Files"
  modal; "Submit Without Files" files it, and the URL becomes `/feedback/<FB number>`.
- **Native confirm dialogs freeze `Runtime.evaluate`** (the Feedback Assistant draft
  Delete, for example). Click from a `setTimeout`, then wait for
  `Page.javascriptDialogOpening` on a session that already ran `Page.enable`, and answer
  it with `Page.handleJavaScriptDialog`.
- **DTS code-level request** (`developer.apple.com/contact/request/code-level-support/`):
  it requires Steps to Reproduce, the Xcode and macOS versions, and a runtime platform
  plus version. Get those approved along with the description before you start.
- **DTS needs a test project:** answering "No, I don't have a project" hides the whole
  request section and the Submit button. The section shows for a moment if you fill
  fast, but it's gone a few seconds later and the hidden fields read `null`. A DTS
  request requires answering Yes (the project goes in a reply to the acknowledgement
  email). Without a project, the request belongs on the Developer Forums. Answering Yes
  and then "Problems with an Apple framework API" brings back the request fields. There's
  no upload field. The page only says "Your request has been sent", and the Case-ID
  arrives within about a minute in an email from dts-help@apple.com. Reply in that
  thread with the zipped project (max 50 MB, about 250 source lines).
- **Developer Forums first-time setup** (`developer.apple.com/forums`, then your profile):
  the first post asks for Forums Setup: pick a public username (shown on every post, not
  your Apple ID name), accept the forums agreement, and save the profile page. On
  2026-10-31 a hidden lease tab got stuck on the profile step: "Get started" opened a
  blank page and "Remind me later" returned 401, and the hidden tab's forum session read
  signed out while Matt's visible tab read signed in. One click on Get started in a
  VISIBLE tab worked. The earlier clicks were script clicks, not trusted input. Use
  `bb-click` (trusted) first; if it still shows blank/401, that step needs a visible tab:
  `bb-click --visible --matt-ok <owner> "Get started"` once he says take over.
- **Signing over SSH on the mini fails** with `errSecInternalComponent` (locked login
  keychain). Let automatic signing there register the bundle ID and capability, then
  sign on a Mac with an unlocked keychain using a manual profile made through the ASC API.

## Google Play Console

- **Replying to a review (verified 2026-10-23, hidden lease works):** the Play service
  account is read-only for replies, so use the logged-in browser. A cold
  `/console/u/0/developers` shows an account picker (`material-select-item`, click it).
  Reviews live at `/console/u/0/developers/<devId>/app/<appId>/user-feedback/reviews`.
  Each `<review>` element (author in `.author-display-name`) has an always-open textarea
  in `<review-reply>` (350-char limit). Set it with the native `HTMLTextAreaElement` value
  setter plus `input`/`change` events; the counter updates. The **Publish reply** and
  Discard buttons only render after the textarea gets focus: call `ta.focus()`, dispatch
  `focus` + bubbling `focusin`, and `ta.click()`, then `button.click()` on "Publish reply".
  Success: the block shows "Edit reply" with the text; reload and read it back to confirm.

## Chat assistants (Grok, ChatGPT, Gemini, DeepSeek)

- **One tab per job, reused across steps:** to post into a chat the human already has open
  (Grok, ChatGPT, etc.), don't open a fresh lease copy for every retry. Keep one lease open
  across type → submit → read-back, and debug inside it. On 10/25 a sender opened the same
  Grok conversation 5 times in 3 minutes (3 visible), and its scripted typing was mistaken
  for human input.
- **Submitting:** `form.requestSubmit()` worked in a hidden tab for Grok and ChatGPT where
  an Enter keypress and a mouse press at the button's coordinates did nothing. Gemini
  needed a dispatched click on its Send button. DeepSeek's send is the empty-text
  `div.ds-button--primary`.
- **Gemini (2026-10-25):** type with `document.execCommand('insertText', ...)` into
  `rich-textarea .ql-editor` after `focus()` (assigning `innerHTML` throws: the page enforces
  Trusted Types). A plain `click()` on "Send message" DOES send, even when the hidden tab's URL
  and `message-content` look unchanged for a while. Never resend on "no reply yet": check the
  sidebar for a new chat title first. One job resent 3 times and made 3 chats. Poll for the
  answer every 10-15s, not every 4s, and give up after a few minutes instead of looping.

- **Asking questions in a private chat from one hidden lease (verified 2026-10-29, ai-shelf job).**
  Fresh chat URLs: ChatGPT `chatgpt.com/?temporary-chat=true`, Claude `claude.ai/new?incognito`,
  Grok `grok.com/c#private`; Gemini needs a `button[aria-label="Temporary chat"]` click; Perplexity
  works logged out. Type with `focus()` + `execCommand('insertText')` into `.ProseMirror` (ChatGPT,
  Claude, Grok), `rich-textarea .ql-editor` (Gemini), `#ask-input` (Perplexity). Send by clicking
  `button[aria-label="Send"]` / `"Send message"` / `"Submit"` / `button[type=submit]` (Grok).
  Answer finished = no Stop button (Claude: no `[data-is-streaming="true"]`) and the answer text
  unchanged across two polls 10s apart. Answer text lives in `[data-dil-message-id]` (ChatGPT, the old
  `data-message-author-role` is gone), `model-response message-content`, `.prose`,
  `.standard-markdown` (Claude; `.font-claude-response` is only a CSS variable now) and
  `.response-content-markdown` (Grok; it matches the user's own bubble too, so drop the element whose
  text equals the question). Citations: ChatGPT shows favicon pills (`img[src*="favicons?domain="]`,
  domain only); Gemini's chips have no href, the URL is base64 inside the `jslog` attribute after
  `BardVeMetadataKey:`; Perplexity's full list appears after clicking the "Sources" button. Free Grok
  has a weekly cap ("You've hit your weekly limit"): read the page for it instead of waiting out a timeout.
- **Perplexity signup and test-chat cleanup (verified 2026-10-29, hidden lease):** logged out, Perplexity answers every
  question with "Sign up and repeat your request." "Continue with Google" navigates the same tab (no popup) to Google's
  account chooser; click `[data-identifier="<email>"]`, then the consent page's "Continue" button. Onboarding is "Get started",
  then a paid-plan page with no free button: `button[aria-label=Close]` declines it and lands on Free plan; dismiss the
  Computer promo with "Maybe later". Deleting a stray chat: Grok `DELETE /rest/app-chat/conversations/soft/<id>` from the
  logged-in tab (list via `/rest/app-chat/conversations`, read the prompt via `load-responses`); Gemini sidebar
  `More options for <title>` → `[data-test-id=delete-button]` → the dialog's Delete.

## OAuth sign-in and news submission sites

- **GitHub "Authorize" button stays disabled in a hidden tab.** GitHub's OAuth page keeps
  the Authorize button disabled until the page looks visible (anti-clickjacking). In the
  same CDP session, call `Emulation.setFocusEmulationEnabled` (enabled: true), wait a
  couple of seconds, then `button.click()`; it enables and the redirect completes
  (verified 2026-10-25).
- **Changelog News (`changelog.com/news/submit`, verified 2026-10-25):** sign-in is
  "via GitHub" (`/auth/github`) or an email magic link. A first GitHub sign-in lands on
  `/join` with name and handle prefilled; it needs an email, and its Turnstile token was
  already filled in the hidden tab. Joining sends a confirmation link (`/in/<hex>`) that
  must be opened before you are signed in. Only Changelog News newsletter subscribers can
  submit: the form bounces to "You must subscribe" until you click Subscribe on the News
  row at `/~` (a data-method POST link, `/~/subscribe?id=16`). The form is URL, Title,
  and "What's interesting about it?" (Markdown, no length limit); `form.requestSubmit()`
  works, and success redirects home with "We received your submission!".

## Application forms (Microsoft Forms and similar)

- **Read the whole form before filling anything (verified 2026-10-25).** Microsoft Forms
  short links (`forms.office.com/r/<id>`) open in a hidden lease with no sign-in and the
  full question list is in `document.body.innerText`. Dump it first and check every
  question against the approved answers; if one needs a fact or a judgment call nobody
  approved (funding history, team, personal details), stop and ask before typing, so a
  half-filled form is never left behind.
- **Multi-step jobs need one lease held open.** Each `with lease(...)` releases on exit,
  and reopening the same site with the same owner is refused after two tries. For a job
  with many steps, run one small holder process that keeps the lease and takes commands
  over a localhost port, then shut it down when the job ends.
- **Microsoft Forms filling (verified 2026-10-25, hidden lease):** each question is a
  `[data-automation-id=questionItem]`. Single-line inputs have NO `type` attribute, so
  `input[type=text]` matches nothing; select `textarea, input:not([type=radio])`. The
  native value setter plus bubbling `input`/`change` events sticks (React state takes
  it). Radios and the agree box are `input[type=radio]`, so `.click()` them. Read every
  field back, then `.click()` the Submit button; the page swaps in place to the form's
  thank-you text (the URL stays the same).

## OAuth popups and custom form widgets

- **An OAuth popup is reachable from the lease that opened it (verified 2026-10-25,
  claude.com Claude for Open Source form).** `window.open` from a leased tab makes a new
  page target with `openerId` set to your tab. `Target.getTargets` on your own session
  lists it, but the broker refuses a direct `/devtools/page/<id>` socket to it. Drive it
  without leaving the broker: `Target.attachToTarget` (flatten: false), then
  `Target.sendMessageToTarget` with `Emulation.setFocusEmulationEnabled` and, a few
  seconds later, a `Runtime.evaluate` (`userGesture: true`) that clicks the GitHub
  "Authorize" button. Replies come back as events, so confirm by watching the popup
  vanish from `Target.getTargets` and the opener page change.
- **A required field you can't see means a widget still wants a click.** HubSpot forms
  with a custom "connect" block keep required inputs hidden (`github_profile`,
  `repository_url`) until the widget finishes. Submit then fails silently, with no error
  text, because the browser can't focus the hidden invalid fields. Run
  `form.checkValidity()` and list `[...form.elements].filter(e => !e.checkValidity())`
  before you submit. On claude.com, picking the repo in the `<select>` wasn't enough;
  the "Use this repository" button is what filled the hidden fields and the verification
  token. Run IIFEs in repeated `Runtime.evaluate` calls: a top-level `const` from an
  earlier call throws on redeclaration and returns nothing.

## Getting a debug port onto an already-running Brave on Windows

- **`MainWindowHandle` finds only one window per process, so a close loop over
  `Get-Process brave` misses the rest (verified 2026-10-26).** Every Brave window belongs
  to the same browser pid, and .NET reports a single "main" handle for it. Posting
  `WM_CLOSE` to that one handle shuts one window and leaves the browser alive, so the
  relaunch is skipped and you learn nothing. Enumerate the windows yourself:
  `EnumWindows` + `IsWindowVisible` + `GetWindowThreadProcessId`, keep the handles whose
  pid is in `(Get-Process brave).Id`, and `PostMessage WM_CLOSE` to each. Then poll until
  `Get-Process brave` is empty before relaunching. Never `Stop-Process` — a killed
  Chromium does not write its session file and the tabs are gone for good.
- **`--restore-last-session` brings back ordinary windows but not `--app=` windows.** A
  site installed as an app window has no entry in the restored session, so after the
  relaunch you have to start it again yourself with `--app=<url>`. Know the app URL
  *before* you close anything; there is no way to read it back once the window is gone.
- **`Start-Process brave` immediately after the last process exits can no-op.** The
  profile lock takes a moment to clear. If CDP never answers, just run the same
  `Start-Process` again a few seconds later rather than adding force-kill logic.

## Etsy listing editor

- **Match variation rows by text, never by input id (verified 2026-10-26).** The editor
  regenerates `variation-quantity-<uuid>` ids on every render, so an id captured before a
  reload is dead. Find the quantity inputs with `/quant/i` against
  `id + name + aria-label`, then read `closest('tr, [role=row], li, div[class*=row]')`
  for the row text.
- **Sizes read as `"L US letter"`, so `/\bL US letter/` isolates Large.** The word
  boundary excludes `XL US letter` (the L is preceded by `X`), and `2X US letter` has no
  L at all. Matching a bare `/L/` silently zeroes XL too.
- **The save button is labelled "Publish changes", not "Save".** A generic
  `/^(save|update)/i` sweep finds nothing and the edit is lost on close. After clicking,
  the editor navigates to the listings index — treat that redirect as the success signal,
  then reopen the editor and re-read the rows to confirm the quantities stuck.
- **Scroll the whole page before reading.** Variation rows are lazy-mounted; a tab that
  was never scrolled reports zero quantity inputs.

## Yahoo Mail (the 2026 "novation" UI)

- **Drive the tab bb-show already put up with `/lease` using the SAME owner and the SAME
  url.** The proxy answers `/adopt` with 410 — agents only ever get tabs the broker made —
  and `/lease` with no url opens a hidden second copy, then closes the shown one when it
  releases. `/lease {"owner":"yahoo-inbox","url":"<the url bb-show got>","hidden":false}`
  returns `shared_with_shown_job: true`, i.e. that exact tab. Release with `close:false`.
- **None of the old `data-test-id` hooks exist.** There is no `selectall-checkbox` and no
  `toolbar-mark-as-read`. Each row is `[data-test-id="message-list-item"]` holding an
  unlabelled `span[role=checkbox]`; selecting any row makes the toolbar grow buttons whose
  only identity is an aria-label: `Mark 44 selected messages as read`. Match
  `/^mark\b.*\bas read$/` on the label and exclude anything containing `unread`, or the
  scorer lands on "Mark as unread".
- **`[data-test-id="unread-indicator"]` stays in the DOM after a row is read.** Counting it
  reports every row unread forever. An unread row's `innerText` starts with
  `Unread message`; the honest total is the page title's `(N unread)`.
- **Sweep the left-nav "Unread" view, not the tab strip.** Primary / Offers / Social /
  Newsletters each hide their own unread, and the strip items are not `[role=tab]`. The
  `Unread` nav link (`[data-test-id="menu-list-item-link"]`) lists all of them in one list,
  where select-all + mark-read empties it in one pass. Note it excludes Archive.
- **The message list is virtual.** Only what is scrolled into view is in the DOM, and of the
  two `virtual-list-container` elements only one actually scrolls — pick the one with
  `scrollHeight > clientHeight`. Loop select → mark → scroll until a sweep picks nothing.
- Script: `%APPDATA%\ineedhemp\_yahoo_markread.py` (`dry` to see the toolbar, `go` to clear).

## Hidden tabs: buttons that only work when the page thinks it is visible

Learned claiming a Lounge.ai profile through GitHub OAuth, 2026-10-30.

- **GitHub's "Authorize" button ships `disabled` and is re-enabled on a timer that never
  fires in a hidden tab.** `document.visibilityState === 'hidden'` throttles the timer, so
  the button stays dead forever and every click silently does nothing. Fix, right after the
  page loads: `Emulation.setFocusEmulationEnabled {enabled: true}` and
  `Page.setWebLifecycleState {state: "active"}`. The button went live in 1 second after that.
  Poll `button[name=authorize][value="1"].disabled` instead of sleeping blind.
- **Probe before you retry.** Two identical "click Authorize" attempts looked like a click
  problem; dumping `[...document.forms]` with `disabled` on every element showed the real
  cause in one pass. Dump the form, do not re-click.
- **Click with `Input.dispatchMouseEvent`, not `el.click()`, on anything guarded.** Scroll
  the element in, take `getBoundingClientRect()`, then press and release at its centre.
- **Type with `Input.insertText` after a real click, never `el.value = x`.** React forms
  (Lounge is Next.js) ignore a value assignment. To replace existing text, send a real
  Ctrl+A first (`Input.dispatchKeyEvent` with `modifiers: 2`), then insert.
- **A hidden lease dies on release, `close:false` or not** — the proxy's `/release` always
  calls `close()` unless the tab is a shown job tab. So a multi-step flow (OAuth hop,
  wizard) must run inside ONE lease, in one script. Splitting the steps across runs lands
  every later step on a fresh `about:blank`. Server-persisted wizard steps are the exception:
  those can be resumed by leasing the step URL again.
