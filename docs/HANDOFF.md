# ExpenseFlow — Handoff

Operational notes for whoever deploys, runs, or maintains this service next. For setup/run/test commands and the endpoint reference, see [`README.md`](../README.md). For schema and design rationale, see [`ARCHITECTURE.md`](ARCHITECTURE.md).

## What it does

ExpenseFlow is a proof-of-concept expense submission and approval API with one journey: submit an expense, normalise it to a base currency (INR), then approve or reject it. It also exposes an AI-generated spending-insights endpoint and ships an optional Streamlit UI on top of the API.

It is explicitly **not production-ready** — see "Known gaps" below before treating it as one.

## How it works

- **Entry point:** `app/main.py` loads `.env`, builds the `FastAPI` app, mounts `app/routes.py`'s router, and calls `init_db()` at import time — table creation happens on process start, not via migrations.
- **Storage:** SQLAlchemy ORM over SQLite (`app/db.py`). The engine URL comes from `DATABASE_URL` (env), defaulting to `sqlite:///expenseflow.db` — a single file next to the app. `check_same_thread=False` is set because Uvicorn serves requests from a thread pool.
- **Data model:** one table, `expenses` (`app/models.py`). Each row holds the original submitted amount/currency, the normalised INR amount, the FX rate used (stored as a string, not a float), a three-state `status` enum (`submitted`/`approved`/`rejected`) backed by a SQLite `CHECK` constraint, and who submitted/decided it plus timestamps. See `docs/ARCHITECTURE.md` §1 for the full column-by-column rationale.
- **Request flow:** `app/schemas.py` defines the pydantic v2 request/response models; `app/routes.py` holds all endpoint logic (no separate service layer — that's a deliberate layout choice per `CLAUDE.md`).
- **State transitions:** approve/reject are implemented as a single atomic `UPDATE ... WHERE id = :id AND status = 'submitted'`, checked via `rowcount`, so concurrent approve/reject calls can't race each other into an inconsistent state. There's no idempotent no-op path — repeat calls on a non-`submitted` expense always return `409`.
- **Insights:** `app/insights.py` calls the Anthropic Messages API (model pinned to `claude-sonnet-4-6`) with a plain-text summary of all expenses and asks for three bullet points back. Any API error is caught and swapped for a fixed fallback string — the endpoint never 500s because of a downstream AI failure.
- **UI:** `ui/app.py` is a standalone Streamlit app that only talks to the API over HTTP (`API_BASE` env var, default `http://127.0.0.1:8000`). It has no direct DB access and holds no business logic — it's a thin client.

## What a deployment engineer needs to know

- **Config is entirely env-driven** (`python-dotenv`, loaded in `app/main.py` and `app/db.py`). Required/optional vars:
  - `ANTHROPIC_API_KEY` — required only for `GET /reports/insights`; missing key just degrades that one endpoint to the fallback text, it doesn't crash startup.
  - `DATABASE_URL` — optional, defaults to a local SQLite file. Point this at a real DSN before scaling past one process (see below).
  - `API_BASE` — only consumed by the Streamlit UI, not the API.
- **SQLite is a single-writer bottleneck.** The default `expenseflow.db` file works for a demo or single-instance deployment but will not tolerate multiple API processes/replicas writing concurrently. Before deploying with more than one worker or replica, point `DATABASE_URL` at a real server (e.g. Postgres) — no code changes are needed beyond the connection string and dropping `connect_args={"check_same_thread": False}` (SQLite-specific), since the app is already using SQLAlchemy's engine abstraction.
- **No migrations.** `Base.metadata.create_all()` only ever *adds* missing tables; it does not alter existing ones. Any future schema change needs a real migration tool (e.g. Alembic) introduced deliberately — don't assume redeploying picks up column changes.
- **No auth.** `submitted_by` and `decided_by` are free-text strings supplied by the caller, not verified identities. Anyone who can reach the API can submit, approve, or reject as anyone. This is fine for an internal PoC behind existing network controls; it is not fine to expose publicly as-is.
- **FX conversion is a placeholder, not a bug to route around.** `POST /expenses` currently sets `amount_base_minor = amount_minor` and `fx_rate = "1"` for every currency (see the `TODO` in `app/routes.py`). There is no live rate lookup yet, so **do not treat `amount_base_minor` as a real INR conversion** until that TODO is implemented — anything downstream (reporting, reconciliation) that assumes real FX conversion will be wrong today.
- **Anthropic API calls are synchronous and unbounded by a request timeout** in `app/insights.py` — a slow/hanging call to the Anthropic API will hold that request open. The Streamlit UI sets a 60s client-side timeout for this endpoint; there's no equivalent server-side timeout, so watch for slow requests piling up if this endpoint gets real traffic.
- **No rate limiting, no pagination.** `GET /expenses` returns every matching row in one response — fine at PoC data volumes, will need pagination before this dataset grows large.
- **Process model:** a single Uvicorn process reading one SQLite file is the only configuration this has been built/tested against. Horizontal scaling requires the database change above first.

## Known gaps (do not assume these exist)

- No tests currently exist in the repo (pytest is installed and wired via `README.md`, but no `test_*.py` files exist yet).
- No live FX rate integration (see above).
- No auth/authorization layer.
- No database migrations.
- No `requirements.txt`/`pyproject.toml` — dependencies are installed ad hoc per `README.md`.
