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

- **X (Twitter) composer:** Draft.js ignores `Input.insertText` AND `document.execCommand`
  in a background tab, so the composer stays empty. Show the composer as a visible tab
  (`bb-show https://x.com/compose/post`, ideally on a machine nobody is using), then type
  with `Input.insertText`, attach with `DOM.setFileInputFiles` on
  `input[data-testid="fileInput"]`, and click `tweetButton`. Check the attachment `img`
  appears before you post.

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
