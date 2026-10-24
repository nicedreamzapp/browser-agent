# Site recipes: what real sites need from an agent

Hard-won notes from driving real, logged-in sites through the broker. Every entry here
cost a failed run first. Add to this file the moment you learn something; that is the
rule, so the next agent doesn't pay for the same lesson.

## Driving any tab

- **Background tabs don't take real input.** A hidden (broker) tab isn't focused, so
  `Input.insertText` and `Input.dispatchMouseEvent` often go nowhere. Fall back to
  `element.click()` for buttons and to setting `.value` and firing `input`/`change`
  events for fields. On a tab the person is looking at, real input (`Input.insertText`)
  is the better choice, because many forms ignore values set from script.
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

- **X (Twitter) composer:** Draft.js ignores `Input.insertText` AND `document.execCommand`
  in a background tab, so the composer stays empty. Show the composer as a visible tab
  (`bb-show https://x.com/compose/post`, ideally on a machine nobody is using), then type
  with `Input.insertText`, attach with `DOM.setFileInputFiles` on
  `input[data-testid="fileInput"]`, and click `tweetButton`. Check the attachment `img`
  appears before you post.

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
- **Signing over SSH on the mini fails** with `errSecInternalComponent` (locked login
  keychain). Let automatic signing there register the bundle ID and capability, then
  sign on a Mac with an unlocked keychain using a manual profile made through the ASC API.
