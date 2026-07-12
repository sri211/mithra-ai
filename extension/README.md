# Mithra Auto-Apply — Browser Extension

Applies to jobs **inside your own logged-in browser**, so LinkedIn/Naukri never
see a "robot on a server" and never block it. This is the only architecture that
reliably auto-applies on LinkedIn.

## Why an extension (and not the server)

A website can't fill another website's form (browser security), and a server
browser gets blocked by anti-bot walls. An extension is the one thing allowed to
fill forms in your real, already-authenticated browser — no login step, no bot
detection, no CAPTCHA walls.

## Install (developer mode — no Web Store wait)

1. Open **chrome://extensions** in Chrome or Edge.
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and select this `extension/` folder.
4. Pin the **✦ Mithra** icon to your toolbar.

## Connect your account (one-time)

1. Go to **https://mithraai.in** and sign in.
2. The extension auto-reads your session — click the ✦ Mithra icon to confirm it
   says **Connected**. (If not, keep the Mithra tab open and click the icon again.)

## Use it

1. Open any job on LinkedIn, Naukri, Indeed, Greenhouse, Lever, Workday, etc.
2. The **✦ Mithra** panel appears bottom-right.
3. Click **Auto-Fill & Apply** — it fills your details, attaches your resume,
   answers screening questions (asks you for anything novel), then shows a
   **Review → Submit** step. You stay in control of the final click.
4. Submitted jobs are added to your Mithra **Tracker** automatically.

## Blockages handled

| Blockage | Solution |
|---|---|
| LinkedIn blocks server logins | Runs in your own logged-in browser — no login needed |
| Website can't script another site | Extension is permitted to; content script fills the DOM |
| React/Vue ignore `input.value` | Native setter + input/change events |
| Can't set file inputs from script | `DataTransfer` rebuilds the resume PDF into the file input |
| Multi-step Easy Apply | Loop: fill → Next → fill → … → Submit |
| Custom dropdowns | Click-open + click best option |
| Screening questions | Cached answers + ask-once for novel ones |
| CAPTCHA on submit | Pauses so you solve it (you're right there) |
| Anti-bot behavioural checks | Human-like typing delays + confirm-before-submit |
| Token expiry | Re-reads token from mithraai.in; popup shows status |

## Notes

- Desktop Chrome/Edge only (extensions don't run on mobile browsers).
- Each submitted application costs the usual auto-apply credits.
- For a public launch, this folder is submitted to the Chrome Web Store (review
  ~1–2 weeks); developer-mode install works immediately for you and beta users.
