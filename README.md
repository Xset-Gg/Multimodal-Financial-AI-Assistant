# SpendLens AI

A multimodal AI assistant for credit-card statements. Upload a statement as a **PDF or image**, and SpendLens
extracts its transactions with **Docling** (layout + table recognition) and a **Groq vision model** (for scans and
photos), normalizes and validates them, stores them in **PostgreSQL**, and answers natural-language questions
with figures **calculated deterministically from your stored transactions**, each traceable to the rows behind it.

> Extraction is automated and can be wrong. SpendLens never claims 100% accuracy: uncertain fields are flagged
> for review instead of being guessed, and every answer links to its source transactions.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Quick start (Docker)](#quick-start-docker)
- [Local development](#local-development)
- [Configuration](#configuration)
- [How extraction works](#how-extraction-works)
- [How the assistant answers](#how-the-assistant-answers)
- [Security & privacy](#security--privacy)
- [Testing & quality](#testing--quality)
- [API overview](#api-overview)
- [Repository layout](#repository-layout)
- [Known limitations](#known-limitations)

## What it does

| Page | Purpose |
|---|---|
| **Landing** (`/`) | Product overview, pipeline and privacy explanation |
| **Dashboard** (`/dashboard`) | Uploaded statements with live status, total spending, transaction count, date range, breakdowns. One-click **demo statement** |
| **Upload** (`/upload`) | Drag-and-drop or file picker, client + server validation, real byte-level upload progress, live stages *Uploading → Extracting → Structuring → Verifying → Ready* with the worker's event log |
| **Statement workspace** (`/statements/:id`) | The original document **side by side** with extracted transactions; verification checks against the statement's printed totals; raw extracted text/tables per page; processing log; AI chat panel |
| **Transaction explorer** (`/transactions`) | Search, sort, filter by statement, date range, merchant, category, currency, min/max amount, type and review status; backend-computed totals for the filtered set; CSV export; per-row details with the original extracted text; user corrections |
| **Analytics** (`/analytics`) | Spending over time (daily/monthly, with table view), by category, top merchants, transaction-size distribution, currency breakdown, all from extracted data |
| **Assistant** (`/assistant`) | Full-page chat scoped to one statement or all of them |
| **Privacy & settings** (`/settings`) | Retention period, delete-everything, what is sent to which model, activity (audit) log |

Everything shown in the UI comes from real backend state: statuses, event logs, counts, charts and answers.

## Architecture

```
 Browser ──► Next.js (3020) ── /api/* rewrite ──► FastAPI (8020) ──► PostgreSQL
   │  React + TS + Tailwind                          │   ▲
   │  SSE: processing + chat streaming               │   │ jobs table (FOR UPDATE SKIP LOCKED)
   └──────────────────────────────────────────────── │ ──┴─► Worker (embedded thread or separate process)
                                                     │          │
                                                     │          ├─ Docling (local): layout + tables
                                                     │          ├─ pdfium text layer (cross-check)
                                                     │          └─ Groq vision (scans/images, optional)
                                                     └─ Groq text model (question → query, optional)
```

**Pipeline:** Upload → file validation → Docling extraction → vision fallback → transaction parsing →
normalization → validation → PostgreSQL → question answering.

- **Background processing.** Uploads create an `analysis_jobs` row; a worker claims it with
  `SELECT … FOR UPDATE SKIP LOCKED`, so several workers can run safely. Crashed jobs are re-queued; the worker also
  purges expired statements.
- **Real-time updates.** Every stage writes a `processing_events` row. `GET /api/statements/:id/stream` is a
  Server-Sent Events endpoint that tails those rows, so the UI shows exactly what the worker did (with `Last-Event-ID`
  resume and automatic reconnect).
- **Data model** (SQLAlchemy + Alembic): `user_sessions`, `statements`, `documents`, `extracted_pages`,
  `transactions`, `analysis_jobs`, `processing_events`, `audit_events`, `chat_conversations`, `chat_messages`.

## Quick start (Docker)

```bash
cp backend/.env.example backend/.env      # optionally set GROQ_API_KEY
docker compose up --build
```

Open <http://localhost:3020>, then **Load demo statement** on the dashboard.

Services: `db` (Postgres 16, host port 5446), `backend` (runs migrations, then the API on 8020), `worker`
(the analysis worker), `frontend` (Next.js standalone on 3020). The backend image pre-downloads Docling's models
at build time, and uses CPU-only PyTorch.

## Local development

Prerequisites: Python 3.12, Node 20.9+, Docker (for Postgres).

```bash
# database (also creates the spendlens_test database used by the test suite)
docker compose up -d db

# backend
cd backend
python -m venv .venv && .venv/Scripts/activate      # or: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --port 8020                     # the worker runs in-process (EMBEDDED_WORKER=true)

# frontend (new terminal)
cd frontend
npm install
npm run dev                                          # http://localhost:3020
```

The first document processed downloads Docling's layout/table models (~1 minute); later runs take seconds.

**Demo data.** `app/demo/demo_statement.pdf` is a fictional 2-page QAR statement (with USD/EUR/GBP purchases,
a payment, a refund, two identical same-day orders and a 0.00 interest line). It is regenerated with
`python scripts/generate_demo_statement.py`, which also writes the ground truth the integration tests check against.
Demo statements run through the **real** pipeline and are labelled *Demo data* everywhere.
`python -m scripts.seed_demo` seeds a workspace from the command line.

## Configuration

All settings are environment variables (see [`backend/.env.example`](backend/.env.example)). Nothing sensitive is
hardcoded, and API keys are only ever read server-side.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://spendlens:spendlens@localhost:5446/spendlens` | PostgreSQL |
| `GROQ_API_KEY` | *(empty)* | Enables vision extraction and LLM question interpretation |
| `GROQ_VISION_MODEL` | `meta-llama/llama-4-scout-17b-16e-instruct` | Vision model for scans/images |
| `GROQ_TEXT_MODEL` | `llama-3.3-70b-versatile` | Model that interprets questions / phrases answers |
| `STORAGE_DIR` | `backend/var/uploads` | Where uploaded files are stored |
| `MAX_UPLOAD_MB` / `MAX_PDF_PAGES` / `MAX_IMAGE_PIXELS` | `20` / `60` / `40000000` | Upload limits |
| `DEFAULT_RETENTION_DAYS` | `7` | Auto-deletion period for new sessions |
| `EMBEDDED_WORKER` | `true` | Run the worker inside the API process |
| `DOCLING_TABLE_STRUCTURE` | `true` | Docling table recognition |
| `SCANNED_PAGE_CHAR_THRESHOLD` | `40` | Pages with less text are treated as image-only |
| `VISION_MAX_PAGES` / `VISION_RENDER_DPI` | `20` / `150` | Vision fallback limits |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |

Groq model names change over time; if a default is retired, set the variable to a current vision/text model.

**Without a Groq key** the app is still fully functional for text-based PDFs (Docling + deterministic parsing, and a
rule-based question interpreter). Image uploads and scanned pages fail with an explicit message telling you to
configure `GROQ_API_KEY`; nothing is faked.

## How extraction works

1. **Validation** (`services/storage.py`): type from magic bytes (PDF/PNG/JPEG/WEBP), size limit, PDF opens and page
   count ≤ limit (password-protected PDFs are rejected), image pixel limit (decompression-bomb guard).
2. **Docling** (`processing/docling_extractor.py`): layout analysis + TableFormer. Tables are kept as cell grids;
   text items are regrouped into visual lines using their bounding boxes (statements print one transaction per
   row even when the layout model splits it into columns), and merged blocks are split at transaction boundaries.
3. **Vision fallback** (`processing/vision.py`): pages that are image-only, or that contain date+amount lines no
   parser could read, are rendered and sent to the Groq vision model, which *transcribes* rows as printed strings
   (it is told never to compute or infer, and to list unreadable fields). Image uploads go straight here.
4. **Parsing** (`processing/parsing.py`): no bank templates. Tables are mapped by header keywords
   (date / posting date / description / amount / debit / credit / currency / foreign amount), or by column content
   when there is no header. Free text is parsed line by line (leading date(s), description, amount(s)), including
   foreign-currency detail lines. Summary rows (totals, balances, due dates) are excluded.
5. **Normalization** (`processing/normalize.py`, `amounts.py`, `dates.py`, `categorize.py`): one schema:
   date, posting date, merchant, description, amount, currency, original amount/currency, debit/credit, category,
   page and extraction method, the original raw text, a confidence score and `uncertain_fields`. Handles
   `1,234.56` / `1.234,56`, `(12.00)`, `12.00 CR`, 3-decimal currencies, many date formats, day/month order detected
   per document (and flagged when it can't be), year inference across new year.
6. **Validation** (`processing/validate.py`): impossible/future/out-of-period dates, zero or unusually large amounts,
   the same printed row captured twice by different passes (merged), identical rows (kept but flagged as possible
   duplicates, since the user decides), extracted sums vs the statement's printed totals, balance reconciliation,
   and an independent check that each amount appears in the PDF's own text layer.

## How the assistant answers

```
question ──► interpreter ──► QuerySpec (validated JSON) ──► deterministic executor ──► result + transaction IDs
              (Groq LLM, or rule parser)                     (SQL filters, Decimal math)          │
                                                                                                  ▼
                                                        explanation (LLM may only restate computed facts)
```

- The LLM **never produces numbers**. It only maps the question to a `QuerySpec` (intent, date range, merchant,
  categories, currency, amount bounds, grouping…), which is schema-validated; invalid output falls back to the
  rule-based parser. Relative dates ("this month") resolve against the statement's data, not today's date, and the
  interpretation is shown to the user.
- Totals are computed per currency with `Decimal`; different currencies are never summed.
- The explanation is streamed, then **checked**: if it contains any number not present in the computed facts, it is
  discarded and the deterministic explanation is used instead.
- Every answer carries the IDs of the transactions it used ("View transactions used for this answer"), filters
  applied, interpretation notes, and warnings when any used row is flagged for review.
- If nothing matches, or the question needs data a statement doesn't contain (credit score, advice, forecasts),
  the assistant says so explicitly. Statement summary figures (minimum due, new balance…) are answered from what
  was printed on the statement, labelled as such.

## Security & privacy

- Uploads stored under random internal keys (`<32 hex>.<ext>`) in `STORAGE_DIR`, created with `O_EXCL` and mode 0600;
  keys are regex-validated and resolved paths must stay inside the root (no path traversal). User filenames are
  sanitized and used only for display. Filesystem paths never appear in API responses.
- File downloads are served with `nosniff`, `Content-Security-Policy: sandbox`, `no-store`.
- Anonymous workspace per browser: random token in an `HttpOnly`, `SameSite=Lax` cookie; only its SHA-256 is stored.
  Every query is scoped to the session; other sessions get 404 (existence isn't leaked).
- CSRF defence: state-changing requests must carry the `x-spendlens-client` header.
- Statement contents are never logged; a redaction filter masks card-number-like digit runs and API keys as a
  second line of defence. Processing events contain only stages and counts.
- Retention: statements auto-delete after 1/7/30/90 days (user-selectable); users can delete a statement or all data.
  Deletion removes the file, pages, transactions and related chats, and records a content-free audit entry.
- Only page images that need vision, and (for questions) merchant names/categories/date range, are sent to Groq.
  No bank or payment-account connections exist.

Recommended for production: HTTPS with `SESSION_COOKIE_SECURE=true`, encrypted volume for `STORAGE_DIR`, real user
authentication in front of the session model, and rate limiting at the proxy.

## Testing & quality

```bash
cd backend
pytest                 # 117 tests; needs the Postgres from docker compose (uses the spendlens_test DB)
ruff check app tests scripts
mypy app

cd frontend
npm run lint && npm run typecheck && npm test
```

Backend tests cover amount/date parsing, table and line parsing across layouts, merchant cleanup and
categorization, normalization and uncertainty flags, duplicate detection, totals cross-checks, the question parser
(including all example questions), LLM-output validation and fallback, numeric grounding of explanations,
deterministic execution, API validation, security-sensitive file handling (traversal, magic bytes, size/pixel
limits, session isolation, headers, deletion, retention purge), and **integration tests** that upload the demo PDF,
run the real Docling pipeline, compare every extracted row with ground truth, check all verification checks pass,
ask questions over the SSE chat endpoint, and delete everything. A second, structurally different statement (US format,
month-first dates, no table grid, rows merged into one text block by the layout model, foreign amount on a continuation
line, several totals per line) must also extract exactly and pass every cross-check. Image and scanned-PDF flows use
a fake vision model. Frontend tests (Vitest) cover the SSE parser, money formatting and query building.

## API overview

| Method & path | Description |
|---|---|
| `POST /api/session` | Create/return the anonymous session (sets cookie) |
| `GET /api/session` · `PUT /api/session/retention` · `DELETE /api/session/data` · `GET /api/session/audit` | Session info, retention, delete all, audit log |
| `GET/POST /api/statements` · `POST /api/statements/demo` | List / upload (multipart `file`) / load demo |
| `GET/DELETE /api/statements/:id` · `POST /api/statements/:id/retry` | Detail (incl. verification), delete, retry |
| `GET /api/statements/:id/file` · `/pages` · `/events` · `/stream` | Original file, extracted pages, events, SSE |
| `GET /api/transactions` · `POST /api/transactions/lookup` · `PATCH /api/transactions/:id` | Filter/sort/page (with totals), fetch by IDs, correct/verify/exclude |
| `GET /api/analytics` · `GET /api/categories` | Aggregations, category list |
| `GET/POST /api/conversations` · `GET/DELETE /api/conversations/:id` · `POST /api/conversations/:id/messages` | Chat (answers stream as SSE) |
| `GET /api/health` | Health (database, Docling, Groq availability) |

Interactive docs: <http://localhost:8020/api/docs> (disabled when `ENVIRONMENT=production`).

## Repository layout

```
backend/
  app/
    api/routes/        session, statements (upload, SSE), transactions & analytics, chat
    ai/                spec (QuerySpec), rules (parser), interpret (LLM + fallback), execute, compose, llm
    processing/        docling_extractor, vision, parsing, amounts, dates, metadata, normalize, validate, categorize, pipeline
    services/          storage (secure files), statements (lifecycle/retention), query (filters/analytics), events
    jobs/worker.py     DB-backed job worker (+ retention sweep)
    models/ schemas/ core/ (config, logging, security) db/
    demo/              fictional demo statement + ground truth
  alembic/             migrations
  scripts/             demo generator, seed, model warm-up
  tests/
frontend/
  src/app/             landing + (app)/ dashboard, upload, statements/[id], transactions, analytics, assistant, settings
  src/components/      AppShell, ChatPanel, Charts, Processing, Transactions, ui
  src/lib/             typed API client, SSE reader, hooks, formatting
docker-compose.yml
```

## Known limitations

- Rule-based categorization is keyword-driven; unmatched merchants are *Uncategorized* (users can recategorize).
- Docling runs without OCR; image-only pages need the Groq vision model.
- Day/month order is inferred per document; when no date disambiguates it, affected dates are flagged.
- Multi-currency totals are reported per currency; no FX conversion is performed (by design).
- The anonymous session model suits a single-user/self-hosted deployment; add authentication for multi-user hosting.
