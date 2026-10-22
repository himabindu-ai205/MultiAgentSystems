# Problem Statement: Multi-Agent Support Reply System for GROWW

## Goal

Build a multi-agent system for the **GROWW** app ([Play Store listing](https://play.google.com/store/apps/details?id=com.nextbillion.groww&hl=en_IN)). It takes a real user question or complaint and drafts a support reply.

- Three specialist agents run **in parallel**. Facts finds the answer, Pattern checks whether it is a known issue, and Compliance checks the draft is safe to send.
- An **Orchestrator** merges their outputs into a reply draft and an internal routing note.
- A **Reviewer** blocks anything that breaks the facts-only rule before a human sees it.

## Why multiple agents

Answering something like *"why was I charged an exit load?"* needs three things at once, and they come from different places:

1. **The fact** lives in a scheme document.
2. **Whether it is a pattern** decides whether this is a reply or an escalation. For example, forty other users may have asked the same thing this month.
3. **Compliance.** The draft must not give investment advice, quote a return figure, or repeat back a PAN the user pasted in.

A single model asked to do all three does the first and forgets the rest. It writes a fluent, uncited paragraph ending in *"you may want to consider staying invested longer"*. That is advice, so it turns a support ticket into a compliance problem.

Each specialist still gets its own moel car. The model only classifies. Facts code copies the setence from a scheme file, Pattern code counts the reviews, and Compliance code redacts PII. The three calls are three nodes on one LangGraph, started together.

## Data

`data/play_store.csv` is **no longer in the project**. The `data/` folder now holds two JSON files and a `parsed/` subfolder:

```
data/
├── normalized_reviews.json   # 1,650 Play Store reviews (complaint source)
├── analyzed_themes.json      # 5 review themes (Pattern Agent source)
└── parsed/                   # 5 scheme pages + manifest (Facts Agent corpus)
    ├── parse_manifest.json
    ├── large_cap.json
    ├── mid_cap.json
    ├── small_cap.json
    ├── gold_fof.json
    └── silver_fof.json
```

### 1. `data/normalized_reviews.json`: the complaint source

This is the source of real user complaints. It is a JSON list of **1,650 Google Play reviews** for GROWW, all with `source = "play_store"`.

| Field | Notes |
|---|---|
| `id` | 16-character hex review ID. It is the key used by the themes file. |
| `source` | Always `play_store`. |
| `rating` | 1 to 5. Distribution: 1★ = 680, 2★ = 95, 3★ = 113, 4★ = 157, 5★ = 605. |
| `title` | Always empty. |
| `text` | The review body. It is free text, often short, and mixes English and Hinglish (e.g. *"frud aap hai 10 mese 2 ka payment milta hai"*). |
| `date` | ISO timestamp, from **2026-04-12 to 2026-07-04**. |
| `version` | App version. It is missing on 227 reviews. |

- Reviews per month: Apr = 362, May = 573, Jun = 622, Jul (to the 4th) = 93.
- There are no developer replies, and no theme, sentiment or severity columns. Those were in the old CSV.
- A regex scan found **no PAN-, phone- or email-like strings** in the review text. The PII test cases therefore have to be **synthetic**. Take a real review and inject a fake PAN, phone number, Aadhaar, email or OTP.

### 2. `data/analyzed_themes.json`: the Pattern Agent's source (Milestone 2 output)

Top-level keys: `reviewCount` (1650), `allThemes` (5 entries) and `topThemes` (3 entries).

Each theme has `id`, `name`, `description`, `reviewIds`, `count`, `avgRating` and `score`. Every review belongs to **exactly one theme**, so there is no overlap and all 1,650 are assigned.

| Theme `id` | Name | Reviews |
|---|---|---|
| `other` | Other feedback | 709 |
| `trading_charts` | Trading, charts & orders | 503 |
| `money_movement` | Withdrawals, deposits & payments | 162 |
| `reliability` | App stability & performance | 140 |
| `fees_brokerage` | Fees & brokerage | 136 |

- `topThemes` is `trading_charts`, `money_movement` and `reliability`.
- `other` is a catch-all and `fees_brokerage` is not a top theme.
- There is no per-review timestamp in this file. To get the count **within a time window**, join `reviewIds` to `normalized_reviews.json` on `id` and use `date`.
- The themes are broad app-feedback buckets. They are not fine-grained issues such as "exit load complaint". Only 2 reviews mention "exit load". The Pattern Agent should therefore match a complaint to the closest theme and count within it. Where possible it should also keyword-match inside the theme.
- **Anchor windows to the dataset**, not to today's date. The latest review is 2026-07-04, so "this week" or "last week" means the 7-day window ending on that date.

### 3. `data/parsed/`: the Milestone 1 facts corpus (Facts Agent)

This holds 5 HDFC mutual fund scheme pages, parsed from public Groww pages. Each scheme has its own file. `parse_manifest.json` indexes them (parsed 2026-06-28, NAV date 25-Jun-2026).

| `scheme_id` | Scheme | Exit load | Source URL (the citation link) |
|---|---|---|---|
| `large_cap` | HDFC Large Cap Fund Direct Growth | 1% if redeemed within 1 year | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| `mid_cap` | HDFC Mid Cap Fund Direct Growth | 1% if redeemed within 1 year | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |
| `small_cap` | HDFC Small Cap Fund Direct Growth | 1% if redeemed within 1 year | https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth |
| `gold_fof` | HDFC Gold ETF Fund of Fund Direct Plan Growth | 1% if redeemed within 15 days | https://groww.in/mutual-funds/hdfc-gold-etf-fund-of-fund-direct-plan-growth |
| `silver_fof` | HDFC Silver ETF FoF Direct Growth | 1% if redeemed within 15 days | https://groww.in/mutual-funds/hdfc-silver-etf-fof-direct-growth |

- File shape: `scheme_id`, `scheme_name`, `source_url`, `nav_date`, `fetched_at`, `text_block_count` and `text_blocks`.
- Each file has 7 `text_blocks`, each with a `section_title` and `text`. The sections are: *Key fund facts* (NAV, expense ratio, minimum SIP and lumpsum, AUM, riskometer, category), *Exit load*, *Benchmark*, *Investment objective*, *Stamp duty and tax*, *Fund management* and *Minimum investments*.
- **`source_url` is the one citation link** per fact. **`fetched_at` / `nav_date` feed the "last checked" date.** All 5 schemes were fetched on 2026-06-28, with a NAV date of 25-Jun-2026.
- **The corpus covers only these 5 schemes and only scheme facts.** Anything else is "not covered" for the Facts Agent. That includes app bugs, KYC status, other AMCs and account-specific questions.
- The manifest is an index only. The facts are in the per-scheme files.

**What each section holds, and how it differs by scheme**

| Section | Content |
|---|---|
| Key fund facts | One text block with NAV, NAV date, expense ratio, minimum SIP and lumpsum, AUM, riskometer and category. |
| Exit load | One line, as in the table above. |
| Benchmark | Large: NIFTY 100 TRI. Mid: NIFTY Midcap 150 TRI. Small: BSE 250 SmallCap TRI. Gold: Domestic Price of Gold. Silver: Domestic Price of Silver. |
| Investment objective | One sentence. Equity funds say "long-term capital appreciation/income" by investing in large, mid or small-cap companies. Gold and Silver say "capital appreciation" by investing in the underlying ETF. |
| Stamp duty and tax | Stamp duty is 0.005% on all five. The **tax rules differ by type**. The three equity funds are taxed at 20% if redeemed within 1 year, and at 12.5% on gains above ₹1.25 lakh per financial year after 1 year. Gold and Silver are taxed at the income-tax slab within 2 years, and at 12.5% after 2 years. |
| Fund management | Long free text with each manager's name, tenure, education and career history, followed by a long list of other schemes they manage. It is noisy, and only the manager names and tenure are useful. |
| Minimum investments | ₹100 for the 1st investment, the 2nd investment and SIP, on all five. |

Key fund facts per scheme:

| Scheme | NAV | Expense ratio | AUM | Riskometer |
|---|---|---|---|---|
| Large Cap | 1217.44 | 1.04% | ₹37,808.31 Cr | Moderately High |
| Mid Cap | 226.915 | 0.75% | ₹97,350.48 Cr | Moderately High |
| Small Cap | 158.068 | 0.77% | ₹38,809.48 Cr | Moderately High |
| Gold FoF | 43.279 | 0.2% | ₹12,121.18 Cr | Moderately High |
| Silver FoF | 35.6537 | 0.22% | ₹4,893.86 Cr | Very High |

**Notes for the Facts Agent**

- **Retrieval.** Retrieve by `scheme_id` and `section_title`. The user has to be asked which scheme, or Facts must say so, when the message doesn't name one. A question like "exit load" without a scheme is ambiguous: the answer is 1% either way, but the window differs (1 year vs 15 days).
- **No performance data.** The corpus holds **no returns, CAGR, ranking or rating data**. The only "return" text is in the tax rules (e.g. "returns are taxed at 20%"). A question like "what returns will I get?" is therefore "not covered" and is also a compliance refusal.
- **Riskometer is a fact, not advice.** It can be quoted with a citation but not used to say a scheme is good or bad.
- **Fund management text is noisy.** It is long and full of other scheme names. Don't put it into a reply verbatim.
- **Staleness.** The data was fetched on 2026-06-28 and NAV is as of 25-Jun-2026, so "last checked" should show these dates and not today's. NAV changes daily and should be treated as stale.

### Fit between the data sources

- Most reviews are about the app: trading, payments, KYC, glitches and brokerage. The corpus only answers scheme-level questions such as exit load, expense ratio, minimum SIP, benchmark and tax.
- So many real complaints will correctly return **"not covered"** from Facts while still getting a strong signal from Pattern.
- To exercise the "corpus answers cleanly" branch, write **scheme-fact questions** such as *"why was I charged an exit load on HDFC Mid Cap?"*. The reviews rarely produce them on their own.

## Architecture / Components

### Three agents (parallel)

None of them waits on or reads another's output.

| Agent | Responsibility | Output |
|---|---|---|
| **Facts Agent** | Retrieves the answer from the **Milestone 1 corpus** (`data/parsed/*.json`). | The answer with **one citation link**. If the corpus doesn't cover it, it returns **"not covered"** and names what it searched. It never falls back on general knowledge. |
| **Pattern Agent** | Checks the complaint against the **Milestone 2 themes** (`data/analyzed_themes.json`, joined to `data/normalized_reviews.json` for dates). | Whether the issue is **recurring, rising or a one-off**, with the **count of reviews** behind it and the **window** used. |
| **Compliance Agent** | Scans the **incoming message and outgoing draft**. | Flags advice language, performance or return claims, and PII. PII types are PAN, Aadhaar, account numbers, phone numbers, emails and OTPs. PII is **redacted before anything is stored**. |

### Orchestrator

Produces two outputs:

1. **Reply draft**
   - Three sentences or fewer, facts-only.
   - Includes its citation link and a **"last checked" date**.
2. **Internal routing note**
   - States which team the ticket goes to and why.
   - Uses the Pattern Agent's signal to set priority.

**Disagreement handling:** if the agents disagree, the orchestrator says so. For example, Facts has a clean answer but Pattern shows forty people asking the same thing this week. It does not send the reply and close the ticket.

### Reviewer

Checks the draft before a human sees it.

- **Hard fails:**
  - an uncited factual claim
  - anything that reads as advice
  - any surviving PII
- On a hard fail, the draft goes back to the orchestrator **once**, with the specific failure named.
- If the second attempt still fails, show the draft **with the flag on it and do not offer the send**.

### Refinement

The human pushes back, and **only the affected agents re-run**. **Log which agents fired.**

| Human feedback | Agent re-run |
|---|---|
| "Shorter" | Orchestrator |
| "They're asking about a different scheme" | Facts |
| "Check if this is the fee issue from last week" | Pattern |

### Approval-gated MCP

This uses the same gate as Milestone 2. **Nothing moves without a human clicking approve.** After approval:

- Append the record to the doc.
- Create the email draft.
- **No auto-send.**

## Rules

- **Facts-only.** No recommendations, no buy or sell language, no return or performance figures.
- **Every factual claim carries one citation link.** No link means it doesn't ship.
- **No PII stored anywhere**, including logs and agent state.
- **Public sources only**, same corpus as Milestone 1.
- **Three agents run in parallel**, shown in the diagram and the demo.
- **Reviewer rework is capped at one round. Refinement is capped at five.**
- **Reply drafts are three sentences or fewer** with a "last checked" date.

## Pre-submission checklist

1. **Run 20 real complaints** from `data/normalized_reviews.json`.
   - Confirm all three agents returned structured output on every one.
   - Confirm every reply either carries a citation or says the corpus doesn't cover it.
2. **Check the branches.** Feed in one complaint of each type:
   - one the corpus answers cleanly
   - one the corpus can't answer
   - one matching a recurring theme
   - one containing a PAN or phone number (**synthetic**, since the reviews contain none)

   Log which agents fired on each, and confirm the reviewer caught the PII.
3. **Try to make it give advice.**
   - Ask whether you should redeem, whether a scheme is good, and what returns to expect.
   - Rephrase, then ask again later in the same session.
   - It should **refuse every time**.

## Dependencies on earlier milestones

- **Milestone 1:** the facts corpus, which is the 5 HDFC scheme pages in `data/parsed/`. The Facts Agent uses it.
- **Milestone 2:** `data/analyzed_themes.json` and `data/normalized_reviews.json`, plus the approval-gated MCP flow (doc append and email draft). The Pattern Agent and the approval step use them.
