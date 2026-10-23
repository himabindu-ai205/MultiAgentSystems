# HDFC Mutual Fund FAQ Assistant

A support desk for factual questions about five HDFC schemes sold on Groww: Large Cap, Mid Cap, Small Cap, Gold FoF, and Silver FoF.

A person pastes one question. Three specialist agents run together and an orchestrator drafts a short reply. A reviewer blocks anything that is not facts-only. Approve writes a support record and an unsent Gmail draft. Nothing is emailed.

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.11 (3.11.9 on Render) |
| Agent graph | [LangGraph](https://github.com/langchain-ai/langgraph) 1.2.0. One fan-out and join. No checkpointer, so a ticket is not stored in the graph. |
| Models | Groq chat completions (`openai/gpt-oss-120b`) for scheme-section labels, theme labels, and draft wording. Gemini (`gemini-3.5-flash`) labels refinement feedback the word rules do not recognise. Compliance and the reviewer currently run on phrase lists (`provider = "none"` in `config/settings.toml`). |
| HTTP client | The Python standard library (`urllib`). No OpenAI or Google SDK. |
| Schemas | Pydantic 2 |
| App server | `http.server.ThreadingHTTPServer` in `src/server.py`. No FastAPI, Flask, or Django. |
| Desk UI | Static HTML, CSS, and JavaScript in `stitch_frontend_design_prototype/`. No React or build step. Type is Fraunces and IBM Plex Sans. |
| Tests | pytest |
| Data | JSON files on disk. No database. |
| Approval tools | A separate Gmail MCP server. This app calls `POST /invoke` for `append_to_google_doc` and `create_email_draft` only. `send_email` is not a method here. |
| Hosting | Render runs the Python process (page and API). The MCP server is its own Render service. Vercel can serve the static page and call the Render API. |

Models only classify or reword a template. Facts copies a sentence from a scheme file. Pattern counts reviews. Compliance redacts and blocks advice. The model is not asked to invent the answer.

## What a question goes through

1. **Ingress** removes personal details (PAN, phone, email, Aadhaar, OTP, account numbers) before any agent or log sees the text.
2. **Facts, Pattern, and Compliance** start together on one LangGraph. None of them reads the others' output.
3. **Orchestrator** merges those results into a reply of at most three sentences, plus one Groww source link and a last-checked date, and an internal routing note (team and priority).
4. **Reviewer** checks the facts-only rules. A failed draft gets one automatic rework. If it still fails, Approve is not offered.
5. **Person** can refine up to five times, approve, or discard.
6. **Approval gate** runs only after an Approve click. It appends the ticket, complaint, reply, citation, and date to a Google Doc, then creates a Gmail draft. The draft is not sent.

Discard drops the in-memory ticket and writes nothing.

## Desk

Open http://127.0.0.1:8765/ after the local server starts.

- **On this desk** lists questions from this browser session. The highlighted row is the one open on the right.
- **Example questions** fill the box and send a scheme fact.
- The reply card spans the right column. The source link and last-checked date sit under the draft.
- **Reviewer: PASS** is not printed on the card. A flag still is, and Approve is hidden unless the reviewer passed.
- **Refine** re-runs the agents. The result line (**Draft re-written.** or **Draft updated.**) appears beside the button.
- **Approve** shows its confirmation above the card. If the Doc or Gmail write fails, the button becomes **Retry write**. The approval stays. The email is still not sent.
- **Discard** shows **Removed from this desk.** above the card, then removes it.

The server holds one ticket at a time. Older cards on the left are kept in the browser.

## API

Same origin as the page when you run `scripts/serve_local.py`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Process is up, and whether a ticket is open |
| POST | `/api/tickets` | Open a ticket from `{"complaint": "..."}` |
| GET | `/api/tickets/current` | The human view of the open ticket |
| POST | `/api/tickets/current/refine` | `{"feedback": "..."}`. Empty feedback re-runs the agents. |
| POST | `/api/tickets/current/approve` | Record approval, then Doc and Gmail draft |
| POST | `/api/tickets/current/retry` | Finish a write that failed after approval |
| POST | `/api/tickets/current/discard` | Drop the ticket |

Responses are the human view: redacted complaint, draft, citation, verdict, and which buttons to show. The raw question is dropped after submit.

## Run it locally

Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install pytest
copy .env.example .env
```

On macOS or Linux, activate with `source .venv/bin/activate` and copy the example file with `cp .env.example .env`.

Set `GROQ_API_KEY` in `.env`. That key is required while `[llm]` in `config/settings.toml` uses Groq. `GOOGLE_API_KEY` (or `GEMINI_API_KEY`) is required for the refinement classifier. `.env` is gitignored.

```bash
python scripts/serve_local.py
```

Then open http://127.0.0.1:8765/.

```bash
python -m pytest
```

Tests that call a live model or the Gmail account are marked `live` and skip when those are not configured.

Local Approve uses `MCP_SERVER_URL` when it is set, otherwise a Node process at `../MCP Server/dist/index.js` if that file and `node` are both present. If neither is available, the write fails closed and the desk shows **Retry write**.

## Configuration

`config/settings.toml` holds model choices, the Google Doc id, the draft recipient, pattern thresholds, and the caps (one reviewer rework, five refinements, three sentences). Empty model fields fall back to the default Groq entry. Set a use to `provider = "none"` to skip that model and stay on rules.

`config/facts.toml`, `config/pattern.toml`, and `config/compliance.toml` hold the phrase lists and scheme matching.

## Data

```
data/
├── normalized_reviews.json    1,650 Groww Play Store reviews
├── analyzed_themes.json       five themes for the Pattern agent
└── parsed/                    five HDFC scheme pages and a manifest
```

Facts reads only `data/parsed/`. There is no general-knowledge fallback. Pattern joins theme ids to review dates. The latest review in the file is the end of the "this week" window, not today's date.

## Deploy

| Host | Role |
|---|---|
| https://multiagentsystems.onrender.com | Page and ticket API. Build and start commands are in `render.yaml`. |
| https://mcp-server-lknb.onrender.com | Gmail MCP server. `GET /health` returns status. The desk calls `POST /invoke`. Opening `/` in a browser returns Not found, because that path is not a page. |

On the FAQ Render service set:

- `GROQ_API_KEY`
- `MCP_SERVER_URL` = `https://mcp-server-lknb.onrender.com` (origin only)
- `MCP_HTTP_TOKEN` = the same bearer token the MCP service expects
- `GOOGLE_API_KEY` if refinement should call Gemini

`PYTHON_VERSION` is `3.11.9` in `render.yaml`.

Vercel, if used, publishes `stitch_frontend_design_prototype` with no build. Away from localhost, `config.js` sends ticket calls to the Render API.

## Layout

```
src/agents/          Facts, Pattern, Compliance
src/orchestrator/    LangGraph fan-out, drafter, routing
src/reviewer/        Facts-only gate
src/refinement/      Human edits, five-round cap
src/approval/        Approve gate and MCP client
src/ingress/         PII redaction
src/llm/             Groq and Gemini calls
src/server.py        Local and Render HTTP server
data/                Reviews, themes, scheme pages
stitch_frontend_design_prototype/   Desk UI
eval/                Recorded evaluation sets
docs/                Problem statement, architecture, plan
```

Design detail is in [docs/problemStatement.md](docs/problemStatement.md) and [docs/architecture.md](docs/architecture.md).
