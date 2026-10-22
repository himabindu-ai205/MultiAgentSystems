# Google Stitch prompt: Groww Support Desk

Paste these into [Google Stitch](https://stitch.withgoogle.com). Stitch works best as **one project prompt**, then **one screen at a time**. Do not dump the whole file into a single generation.

**Project settings in Stitch**

- Medium: **Web**
- Device: **Desktop, 1440×900**
- Model: latest Gemini Pro if offered
- After the project prompt lands, generate screens in the order listed below

---

## 0. Project prompt (paste this first)

```
Design a desktop web app called Groww Support Desk.

This is an internal tool for Groww (Indian investing app) support agents. It is not the customer Groww app, not a chatbot, and not a marketing site. A support person pastes one customer complaint. Three specialist agents (Facts, Pattern, Compliance) run in parallel. An Orchestrator writes a short reply draft. A Reviewer either PASSes it or FLAGs it. The human then refines, approves, or discards. Approve never sends email. Approve only appends a support record to a Google Doc and creates an unsent Gmail draft.

Primary user: a Groww support agent at a desk, reviewing one ticket at a time, deciding whether a draft is safe to leave the building.

Product personality: a compliance desk, not a consumer fintech homepage. Calm, exact, a little severe. The draft the customer might receive is the most important thing on the screen. Everything else is evidence for the gate.

Visual direction:
- Desktop-first 1440px web console. Left-aligned content. Dense but breathable. Not a card farm.
- Paper background #F6F7F4 (cool grey-green, like a printed statement). Ink #1A1F1C. Hairline rules #D5DAD4.
- Groww mint #00B386 is used only for Reviewer PASS and the Approve button. Never as decoration, never as a page wash, never on every card.
- Hold / disagreement uses amber #C47A12. Flag / fail uses vermillion #C23A2B. P1 priority uses the vermillion. P2 uses amber. P3 uses ink at 60% opacity.
- Type: IBM Plex Sans for chrome, labels, routing, and buttons. Source Serif 4 for the customer-facing reply draft only, so the draft reads as a letter, not as UI copy. Request IDs and timestamps use IBM Plex Mono at 12px.
- 4px radius on inputs and small chips. 0 radius on the draft letter panel and on the reviewer gate. No drop shadows. No gradients. No glass. No decorative illustrations. No stock photos of smiling customers. No chatbot bubbles.
- One memorable structure: a three-lane horizontal agent trace (Facts | Pattern | Compliance) with overlapping start ticks, proving they ran together. Below that, a two-column work surface: the letter (complaint + draft) on the left, the gate (routing + reviewer + actions) on the right.

App chrome, every screen:
- Top bar: wordmark "Groww Support Desk" in IBM Plex Sans Medium, 16px. Right side: a small text "Nothing is sent until you approve" in ink at 50%. No user avatar, no settings gear, no bell.
- No sidebar navigation. This is a single-ticket workbench, not a multi-page SaaS app. A text link "New ticket" top-left under the wordmark is enough.
- Footer none.

Hard UI rules (must hold on every screen):
1. Show only the redacted complaint. If PII was found, show typed placeholders such as [PAN], [PHONE], [EMAIL] in a muted chip style, never the original values.
2. Approve is visible only when Reviewer verdict is PASS and the draft is not flagged. If flagged, FAIL, NOT REVIEWED, or there is no draft: do not draw an Approve button at all. Do not grey it out. Omit it.
3. Reply drafts are at most three sentences. Render the citation as a real Groww URL and the last-checked date on its own line under the draft.
4. Buttons: "Refine (N left)", "Approve", "Discard". Sentence case. Approve is the mint filled button. Discard is a text button in vermillion. Refine is an outlined ink button.
5. English UI copy. Complaints may be Hinglish. Do not translate the complaint.

Do not generate a login page, dashboard of many tickets, analytics, settings, or a customer-facing Groww homepage.
```

---

## 1. Design tokens (keep this in the project; do not regenerate as a screen)

Use this as a follow-up after the project prompt if Stitch drifts:

```
Lock the design system and do not change it on later screens.

Colour
- paper: #F6F7F4
- ink: #1A1F1C
- rule: #D5DAD4
- mint (PASS / Approve only): #00B386
- amber (HOLD / P2): #C47A12
- vermillion (FLAG / P1 / Discard): #C23A2B
- placeholder chip fill: #E8EBE6

Type
- Chrome and body: IBM Plex Sans, 13–16px, regular and medium. Line height 1.45.
- Draft letter: Source Serif 4, 18px, regular, line height 1.5, max 62 characters per line.
- IDs and times: IBM Plex Mono, 12px.

Components
- Draft letter: full-bleed panel, 1px ink border, 0 radius, 32px padding, paper slightly lighter than the page (#FBFCF9).
- Gate panel: same border language, no fill change.
- Priority chip: square, 0 radius, 1px border, letters P1 P2 P3.
- Agent lane: three equal columns, hairline dividers, a small filled start-tick on a shared time axis.
- Primary button: mint fill, white text, 4px radius, 40px height.
- Outline button: 1px ink border, 4px radius.
- Text button: no border, vermillion for Discard.

Forbidden: Inter, Geist, rounded 16px cards, soft grey shadows, cream #F4F1EA, terracotta, dark mode, neon green, hero gradients, all-caps eyebrows, middle-dot meta strings, chatbot avatars.
```

---

## 2. Screen prompts

Generate in this order. After each screen, use the short fix prompts in section 3 if needed.

### Screen A — New ticket (intake)

```
Desktop 1440×900. Groww Support Desk, new ticket.

Goal: the support agent pastes one customer complaint and starts the three agents.

Layout:
- Top bar as in the project prompt. Text link "New ticket" is current, so it is ink, not a button.
- Centered column, 720px wide, left-aligned within the column, vertically starting 96px below the top bar. Not a modal.
- Heading in IBM Plex Sans Medium 28px: "Paste the customer complaint"
- Subhead 15px ink at 70%: "Personal details are removed before any agent sees the text. Facts, Pattern and Compliance then run together."
- A tall textarea, 1px ink border, 4px radius, min-height 220px, placeholder: "Why was I charged an exit load on HDFC Mid Cap?"
- Helper under the field, 12px: "English or Hinglish. Do not include PAN, phone, email, Aadhaar, OTP or account numbers if you can avoid it. If they are present they will be replaced with [PAN], [PHONE] and similar."
- Character count bottom-right of the textarea: "0 / 5000"
- Primary action under the field, left-aligned: a mint button labelled "Run agents". It is disabled (ink at 30%, not mint) while the textarea is empty.
- No secondary marketing copy. No sample ticket list. No drag-and-drop.

Empty state of this screen is the default: textarea empty, button disabled.
```

### Screen B — Running (parallel agents)

```
Desktop 1440×900. Groww Support Desk, agents running on one ticket.

Goal: prove the three specialists started together. This is a wait screen, not a chatbot typing indicator.

Use this ticket:
- Request ID: 7f3a9c12-e2b1-4d44-9c0e-1a2b3c4d5e6f (IBM Plex Mono)
- Redacted complaint shown in a quiet one-line strip: "why was I charged an exit load on HDFC Mid Cap?"

Layout:
- Top bar.
- Below it, a full-width three-lane trace, 160px tall, 1px rule top and bottom:
  Lane 1 Facts — status "retrieving scheme page", start tick at t=0.0s
  Lane 2 Pattern — status "counting reviews in window", start tick at t=0.0s
  Lane 3 Compliance — status "scanning for advice and PII", start tick at t=0.0s
  A shared time axis under the lanes labelled 0s — 30s. All three start ticks sit on the same vertical line at 0s. Each lane has a thin in-progress bar, not a spinner in the page centre.
- A small line above the lanes: "Ingress redaction finished. Raw text dropped. Agents started together."
- No draft yet. Do not invent a reply.
- No Approve, Refine, or Discard.
- A text control bottom-left: "Cancel ticket" in ink at 60%.

Do not show a circular loader covering the page. The overlapping start ticks are the point of this screen.
```

### Screen C — Review, SEND_READY (happy path)

```
Desktop 1440×900. Groww Support Desk, ticket review. This is the main screen of the product.

Ticket
- Request ID: 7f3a9c12-e2b1-4d44-9c0e-1a2b3c4d5e6f
- Redacted complaint: "why was I charged an exit load on HDFC Mid Cap?"
- Disposition: SEND_READY
- Reviewer verdict: PASS (mint)
- Flagged: no
- Agents that ran: facts, pattern, compliance, orchestrator
- Refinements left: 5

Reply draft (Source Serif 4, the visual hero):
"HDFC Mid Cap Fund Direct Growth has an exit load of 1% if units are redeemed within 1 year."
Source line under it, IBM Plex Sans 13px: "https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth"
Last checked: 28-Jun-2026

Routing
- Team: Mutual Funds Support
- Priority: P3
- Why: "Reply ready for review. Pattern: one_off on theme fees_brokerage, 2 similar reviews in the 7 days 2026-06-28 to 2026-07-04 and 1 in the 7 days before. Priority P3."

Layout:
- Top bar.
- Three-lane agent trace, now complete, 88px tall. Each lane shows duration (e.g. Facts 412ms, Pattern 388ms, Compliance 401ms) and a filled start tick still aligned at the same t=0. Labels "done".
- Two columns below, 12px gap, no cards-in-cards:
  LEFT 62%: 
    Label "Complaint (personal details removed)" then the complaint in IBM Plex Sans 15px.
    Then the draft letter panel with heading "Reply draft", the serif paragraph, then Source URL, then Last checked.
  RIGHT 38%:
    Heading "Routing"
    Team name, square P3 chip, the why paragraph.
    Heading "Reviewer" with a mint PASS mark, no fill banner.
    "Agents that ran" as a simple inline list: facts · pattern · compliance · orchestrator — wait, do not use middle dots. Use a vertical stack of four short rows instead, each with a 6px mint square.
    Actions stacked at the bottom of the right column, full width of the column:
      Outline button "Refine (5 left)"
      Mint filled button "Approve"
      Vermillion text button "Discard"
- A quiet line under Approve: "Creates a Google Doc record and an unsent Gmail draft. Does not send."

No disagreement banner. No flag. Approve is present.
```

### Screen D — Review, HOLD_DISAGREEMENT (do not close)

```
Same layout as Screen C. Change only the content and the gate.

Ticket
- Complaint: "why was I charged an exit load on HDFC Mid Cap? charts not loading also"
- Disposition: HOLD_DISAGREEMENT
- Reviewer: PASS (the draft is still allowed to exist)
- Priority: P1 square chip in vermillion
- Team: Mutual Funds Support + Trading & Orders Support
- Agents that ran: facts, pattern, compliance, orchestrator
- Refinements left: 5

Draft (still shown, still a letter):
"HDFC Mid Cap Fund Direct Growth has an exit load of 1% if units are redeemed within 1 year."
Source: https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth
Last checked: 28-Jun-2026

A 1px amber rule banner above the two columns, not a toast:
Title: "Do not close this ticket"
Body: "Facts has a clear answer, but Pattern shows a rising issue. The reply answers the question asked and does not address the wider issue, so the ticket must not be closed."

Routing why: "Do not close this ticket. Pattern: rising on theme trading_charts, 12 similar reviews in the 7 days 2026-06-28 to 2026-07-04 and 4 in the 7 days before. Priority P1."

Actions: Refine (5 left), Approve, Discard. Approve may stay, because the Reviewer passed, but the banner is the thing the eye hits first. Do not style Approve as "Send and close".
```

### Screen E — Review, NOT_COVERED (app complaint)

```
Same layout as Screen C.

Ticket
- Complaint: "frud aap hai 10 mese 2 ka payment milta hai"
- Disposition: NOT_COVERED
- Reviewer: PASS
- Team: Payments & Withdrawals Ops
- Priority: P2 (amber chip; money_movement raises priority one level)
- Draft:
"This question is not covered by the scheme pages we have. We searched HDFC Large Cap, Mid Cap, Small Cap, Gold FoF and Silver FoF. A payments team will take this from here."
- Source: (none) — do not invent a URL. Show the last-checked line as omitted, not as today's date.
- Routing why: "The scheme information does not cover this. The reply says so. Pattern: recurring on theme money_movement, 18 similar reviews in the 7 days 2026-06-28 to 2026-07-04 and 11 in the 7 days before. Priority P2. Raised one level because the theme is money_movement. Check whether this is a known issue."

Keep the complaint in Hinglish as written. Do not translate it. Approve is present because Reviewer PASSed a not-covered draft with no factual claim.
```

### Screen F — Review, REFUSAL (advice request)

```
Same layout as Screen C.

Ticket
- Complaint: "Should I redeem my HDFC Mid Cap? Is it a good fund? What returns will I get?"
- Disposition: REFUSAL
- Reviewer: PASS
- Team: Mutual Funds Support + Compliance review
- Priority: P3
- Draft:
"We cannot give advice on whether to redeem, whether a scheme is good, or what returns to expect. HDFC Mid Cap Fund Direct Growth has an exit load of 1% if units are redeemed within 1 year."
- Source: https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth
- Last checked: 28-Jun-2026
- Routing why includes: "Advice or returns request. The reply declines it." and "Advice requests in this session: 1."

A 1px ink rule note in the right column, not a red alarm: "The team cannot give investment advice. The same question rephrased will be refused again."

Approve is present. Do not add a "override and advise" control.
```

### Screen G — Review, HOLD_AGENT_FAILURE (no draft)

```
Same two-column skeleton as Screen C, but the left draft letter panel is empty of a reply.

Ticket
- Complaint: "what is the exit load of HDFC Large Cap"
- Disposition: HOLD_AGENT_FAILURE
- Reviewer: NOT REVIEWED
- Team: General Support
- Priority: P2
- Draft: none
- Agents that ran: facts (error TIMEOUT), pattern (done), compliance (done), orchestrator

Left column:
- Complaint as usual.
- Draft letter panel contains one sentence in IBM Plex Sans, not serif: "No draft: Facts failed (TIMEOUT), so a person needs to look at this ticket." No URL. No last-checked.

Right column:
- Reviewer shows "NOT REVIEWED" in ink, not mint, not vermillion.
- Do not draw an Approve button. Draw only "Refine (5 left)" and "Discard".
- Routing why: "Agent failure: facts (TIMEOUT). No reply was drafted. Priority P2."
```

### Screen H — Review, FLAG (send not offered)

```
Same layout as Screen C.

Ticket
- Complaint: "why was I charged an exit load on HDFC Mid Cap?"
- Disposition: SEND_READY (the orchestrator still produced a draft)
- Reviewer: FAIL
- Flagged: true
- Failure types shown as codes, never quoted draft text: ADVICE_LANGUAGE
- Draft (shown so the human can see why it is blocked):
"HDFC Mid Cap Fund Direct Growth has an exit load of 1% if units are redeemed within 1 year. You may want to consider staying invested longer."
- Source URL still shown. Last checked 28-Jun-2026.

A 1px vermillion rule banner above the columns:
Title: "This draft is flagged"
Body: "It failed review again after the one allowed rework, so it cannot be sent as it is."

Right column:
- Reviewer: FAIL in vermillion, plus "Reasons: ADVICE_LANGUAGE"
- Do not draw an Approve button at all. Do not show a disabled Approve. Only "Refine (5 left)" and "Discard".
```

### Screen I — Refine (feedback)

```
Desktop 1440×900. Groww Support Desk, refine sheet on top of Screen C content.

The review layout stays visible and slightly recedes (no dark modal overlay, no blur). A 520px right-edge sheet, paper fill, 1px ink left border, 0 radius, slides over the gate column.

Sheet heading: "Refine this draft"
Subhead: "4 of 5 refinements remaining after this one. Only the agents that need to move will run again."

A textarea, placeholder examples as three quiet chips the person can click, not as a dropdown:
- "Shorter"
- "They're asking about a different scheme: HDFC Gold FoF"
- "Check if this is the fee issue from last week"

Helper: "Ambiguous notes go to the Orchestrator only. Do not paste PAN, phone or other personal details in this box."

Primary button in the sheet: "Run refinement" (ink fill, white text — not mint; mint is reserved for Approve).
Text button: "Cancel"

Do not show Approve inside the sheet.
```

### Screen J — Refinement cap reached

```
Same as Screen C, SEND_READY, PASS, Approve present.

Change:
- Refinements left: 0
- Do not draw the Refine button.
- In the actions stack, a 13px line: "Refinement limit reached." Then Approve, then Discard.
- A notice under the draft: "The sixth change was refused. You can still approve or discard this draft."
```

### Screen K — PII redacted on the complaint

```
Same as Screen C, but the complaint demonstrates redaction.

Complaint shown:
"please refund to my account, PAN [PAN], phone [PHONE], email [EMAIL]"

Under the complaint, a quiet row of chips (placeholder chip fill, 4px radius): "Redacted before processing: PAN 1, PHONE 1, EMAIL 1."

Draft is a NOT_COVERED payments reply. Reviewer PASS. Approve present.

Never show ABCDE1234F, a real phone number, or a real email. The chips and the square-bracket tokens are the only PII UI.
```

### Screen L — Approved (nothing sent)

```
Desktop 1440×900. Groww Support Desk, ticket closed after Approve.

Top bar still says "Nothing is sent until you approve" — change the right-side text to "Approved. Nothing was sent."

Center, 640px, left-aligned:
Heading: "Record written. Email draft created."
Body, IBM Plex Sans 15px:
"The approved reply was appended to the support Google Doc. A Gmail draft was created for Mutual Funds Support. The email was not sent."

Show a compact recap, not the full workbench:
- Request ID in Plex Mono
- Team, P3, Reviewer PASS
- The serif draft paragraph once more, smaller (16px)
- "Doc append: done"
- "Gmail draft: created (unsent)"

One text link: "New ticket". No Approve, Refine, or Discard. No confetti. No success illustration.
```

### Screen M — Approval failed (retry)

```
Same recap skeleton as Screen L, but the write failed.

Heading: "The record was not written"
Body: "Approval is kept. The Google Doc append failed, so the Gmail draft was not created. Nothing was sent."

Buttons:
- Outline "Retry write"
- Text "Back to ticket"

Do not show a green success state. Do not clear the ticket.
```

### Screen N — Intake error (empty / too long)

```
Same as Screen A, but after a failed submit.

Case: empty complaint.
Textarea has a 1px vermillion border.
Error under the field, 13px vermillion, no icon salad: "Paste a complaint with usable text before running the agents."
Run agents stays disabled.

Also design the too-long variant as a second frame of this screen:
Character count "5001 / 5000" in vermillion.
Error: "This complaint is over 5000 characters. Shorten it. It was not truncated and no agent ran."
```

---

## 3. Follow-up prompts (one change at a time)

Use these in Stitch after a screen is close:

```
On the review screen, make the reply draft the largest type on the page. Chrome stays 13–16px. The draft is Source Serif 4 at 18px.
```

```
Remove every drop shadow and every 16px rounded card. Borders are 1px #D5DAD4 or #1A1F1C. The draft letter and the reviewer gate have 0 radius.
```

```
Delete the Approve button on this screen. Flagged and failed drafts must not show Approve, even disabled.
```

```
Align the three agent start ticks on one vertical line at t=0. They must not stagger.
```

```
Replace any chatbot bubbles with a letter panel: 1px ink border, 32px padding, no avatar, no send icon.
```

```
Change all button labels to sentence case: Refine (5 left), Approve, Discard. No "Submit", no "Send", no "Execute".
```

```
Show the citation as a full groww.in URL on its own line, then "Last checked 28-Jun-2026". Do not use today's date.
```

```
The complaint containing [PAN] [PHONE] [EMAIL] must keep the square brackets. Do not invent realistic dummy PII.
```

```
Add a compact 1024px tablet layout of the review screen: stack the letter above the gate. Keep desktop as the default.
```

---

## 4. Screen map (what to generate)

| Stitch screen | Product state | Approve? | Refine? |
|---|---|---|---|
| A New ticket | Empty intake | no | no |
| B Running | Parallel agents in flight | no | no |
| C Review SEND_READY | Happy path, Reviewer PASS | **yes** | yes |
| D Review HOLD_DISAGREEMENT | Fact + rising pattern | yes, with "do not close" | yes |
| E Review NOT_COVERED | App / payments complaint | yes if PASS | yes |
| F Review REFUSAL | Advice / returns | yes if PASS | yes |
| G Review HOLD_AGENT_FAILURE | No draft | **omit** | yes |
| H Review FLAG | Second Reviewer fail | **omit** | yes |
| I Refine sheet | Feedback | omit in sheet | submit |
| J Cap reached | 0 refinements left | yes if PASS | **omit** |
| K PII chips | Redacted complaint | yes if PASS | yes |
| L Approved | Doc + unsent Gmail draft | omit | omit |
| M Write failed | Retry | omit | omit |
| N Intake error | Empty or too long | no | no |

---

## 5. Copy bank (use verbatim)

**Chrome**

- Groww Support Desk
- Nothing is sent until you approve
- New ticket
- Paste the customer complaint
- Run agents
- Complaint (personal details removed)
- Reply draft
- Source
- Last checked
- Routing
- Reviewer
- Agents that ran
- Refine (5 left)
- Approve
- Discard
- Creates a Google Doc record and an unsent Gmail draft. Does not send.

**Notices**

- Do not close this ticket
- This draft is flagged
- It failed review again after the one allowed rework, so it cannot be sent as it is.
- No draft: an agent failed, so a person needs to look at this ticket
- Refinement limit reached.
- Record written. Email draft created.
- The email was not sent.
- Approval is kept. The Google Doc append failed, so the Gmail draft was not created.

**Teams** (only these)

- Mutual Funds Support
- Trading & Orders Support
- Payments & Withdrawals Ops
- App Technical Support
- Fees & Billing Support
- General Support
- Compliance review

**Priorities:** P1, P2, P3

**Dispositions:** SEND_READY, HOLD_DISAGREEMENT, NOT_COVERED, REFUSAL, HOLD_AGENT_FAILURE

**Verdicts:** PASS, FAIL, NOT REVIEWED

**Failure codes:** UNCITED_CLAIM, ADVICE_LANGUAGE, PII_SURVIVED

---

## 6. What this UI is not

Do not let Stitch add:

- A customer Groww home, watchlist, or scheme purchase flow
- Login / SSO (out of scope for the demo)
- A queue of many tickets (the product is one in-memory ticket)
- Charts of theme volume (Pattern already summarised the count in the routing note)
- A chat thread with the model
- A Send email button
- Today's date as last-checked (corpus was fetched 28-Jun-2026; NAV date 25-Jun-2026)
- Raw PAN / phone / email samples

The web form is the human view specified in `docs/architecture.md` §10–12 and `docs/implementation-plan.md` Phase 8–9. The current app is CLI (`config/settings.toml` `[ui] form = "cli"`). These screens are the web equivalent of `src/refinement/view.py`.
