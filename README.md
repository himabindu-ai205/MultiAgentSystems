# HDFC Mutual Fund FAQ Assistant

A support desk for factual questions about five HDFC schemes on Groww: Large Cap, Mid Cap, Small Cap, Gold FoF, and Silver FoF. Three agents draft a short reply. A reviewer checks it. A person approves it before anything is written down. Approve never sends email.

## What it does

1. The question is redacted. PAN, phone, email, Aadhaar, and similar details are replaced before any agent sees the text.
2. Facts, Pattern, and Compliance run together. Facts copies a sentence from a scheme file. Pattern counts matching Play Store reviews. Compliance blocks advice and performance claims.
3. The orchestrator writes a reply of at most three sentences, with one source link and a last-checked date.
4. The reviewer must pass the draft before Approve is offered.
5. Approve appends a support record to a Google Doc and creates an unsent Gmail draft. Discard saves nothing.

## Run it locally

Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pytest
copy .env.example .env
```

Put `GROQ_API_KEY` in `.env`. Scheme classification and draft wording use Groq. Copy `.env.example` and fill only the providers you turn on in `config/settings.toml`.

```bash
python scripts/serve_local.py
```

Open http://127.0.0.1:8765/. The page and the ticket API are the same process. One ticket is held in memory at a time.

```bash
python -m pytest
```

## Desk

The left column lists questions from this session. The open reply fills the right side. Example questions are there to start from. Refine can be used up to five times. Discard removes the ticket. If the Google write fails after Approve, the button becomes Retry write. The approval is kept and the email is still not sent.

## Deployed

| Host | Role |
|---|---|
| https://multiagentsystems.onrender.com | Page and ticket API (`render.yaml`) |
| https://mcp-server-lknb.onrender.com | Gmail MCP server. Health is `/health`. The desk calls `POST /invoke`. |

On the FAQ service set `GROQ_API_KEY`, `MCP_SERVER_URL` (origin only, no path), and `MCP_HTTP_TOKEN`. The Doc id and draft recipient are in `config/settings.toml`.

## Layout

```
src/agents/          Facts, Pattern, Compliance
src/orchestrator/    Fan-out, draft, routing
src/reviewer/        Facts-only gate
src/refinement/      Human edits, capped at five
src/approval/        Approve, then Doc append and unsent Gmail draft
src/ingress/         PII redaction
data/parsed/         Five scheme pages
data/                Play Store reviews and themes
stitch_frontend_design_prototype/   Desk UI
docs/                Problem statement, architecture, plan
```

Longer design notes are in [docs/problemStatement.md](docs/problemStatement.md) and [docs/architecture.md](docs/architecture.md).
