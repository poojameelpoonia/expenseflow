# ExpenseFlow — Production Gap Audit

ExpenseFlow is explicitly a PoC (`CLAUDE.md`, `docs/HANDOFF.md`). This audits it against a production bar across ten areas. Each gap states what's actually in the code today (not the design intent in `docs/ARCHITECTURE.md`), whether it blocks a real deployment or can be deferred, and a rough effort estimate assuming one engineer familiar with the codebase.

**Blocking** = would cause an incident, breach, or unrecoverable state in normal production operation; must be fixed before real users/real data. **Deferrable** = a real limitation, but tolerable for a controlled/internal rollout with compensating controls (e.g. network isolation), and can follow in a fast second pass.

| # | Area | Verdict | Rough effort |
|---|---|---|---|
| 1 | Auth & key rotation | Blocking | 3–5 days (auth) + ongoing |
| 2 | Input validation | Blocking (partial) | Hours–1 day |
| 3 | Rate limiting | Deferrable*, blocking for `/reports/insights` | 0.5–1 day |
| 4 | Observability & logging | Blocking | 2–3 days |
| 5 | Error handling | Blocking | 1 day |
| 6 | DB migrations & pooling | Blocking (migrations) | 1–2 days |
| 7 | Secrets management | Blocking (immediate risk) | Minutes–2 days |
| 8 | Tests & coverage | Blocking | 3–5+ days |
| 9 | Deployment & health checks | Blocking | 1–3 days |
| 10 | Data privacy | Blocking | 1 day + ongoing |

\* Deferrable only if the deployment stays behind existing network controls per `docs/HANDOFF.md`; not deferrable for anything internet-facing.

---

## 1. Authentication and key rotation

**Gap:** There is no authentication anywhere in the API. Every route in `app/routes.py` is unauthenticated; `submitted_by` and `decided_by` are plain caller-supplied strings (`app/schemas.py`), not verified identities — anyone who can reach the API can submit an expense as "anyone," or approve/reject as "anyone." There's also no distinction between a submitter role and an approver role — the same unauthenticated caller can do both. `ANTHROPIC_API_KEY` is a single static value read once via `python-dotenv` (`app/insights.py`); there is no rotation mechanism, no expiry handling, and no fallback if the key is revoked mid-deployment beyond the existing "fall back to a fixed string" error path.

**Classification:** Blocking. No real expense-approval workflow can ship without knowing who submitted and who approved something, and without preventing a submitter from approving their own expense.

**Effort:** 3–5 days for a baseline (e.g. OAuth2/JWT bearer auth via FastAPI's `Depends`, a minimal user/role model, wiring `submitted_by`/`decided_by` to the authenticated identity instead of a free-text field). Key rotation is smaller in isolation (a process + a secret-manager-backed reference instead of a static env var — see §7) but depends on where secrets end up living.

## 2. Input validation

**Gap:** `ExpenseCreate` (`app/schemas.py`) validates `amount_minor > 0` and `currency` as exactly 3 alphabetic characters, uppercased — but `description`, `category`, `submitted_by` have no length or content constraints at all, so arbitrarily large strings can be stored (and, until §3 is fixed, resubmitted without limit). The currency check also stops at "3 letters," not the ISO-4217/allowlist restriction that `docs/ARCHITECTURE.md` §1 documents as a design decision ("fixed allowlist of 2-decimal-minor-unit currencies") — that allowlist was never actually implemented in code, so today a nonsense code like `"ZZZ"` or a 3-decimal/0-decimal currency (`BHD`, `JPY`) is accepted and silently mis-normalised once real FX conversion lands.

**Classification:** Blocking, but cheap. Missing length caps are a real DoS/storage-bloat vector; the missing currency allowlist is a documented decision that was simply never wired up.

**Effort:** A few hours — add `max_length` to string fields, add the currency allowlist check already specified in the architecture doc.

## 3. Rate limiting

**Gap:** No rate limiting exists at any layer — no middleware (e.g. `slowapi`), no reverse-proxy config, nothing in `app/main.py`. This is a general gap across all endpoints, but it's sharpest on `GET /reports/insights` (`app/routes.py`), which makes a real, billed call to the Anthropic API on every single request with no caching, debouncing, or per-caller limit — an unauthenticated caller (see §1) can currently drive unbounded external API spend by hitting this one endpoint in a loop.

**Classification:** Deferrable for the CRUD endpoints if the deployment stays behind existing network controls, as `docs/HANDOFF.md` already assumes for the no-auth gap. **Not deferrable for `/reports/insights`** specifically, since it has a direct, unbounded cost per call regardless of network exposure.

**Effort:** 0.5–1 day for basic per-IP/per-key request-rate middleware, plus a simple cache/debounce (e.g. don't regenerate insights more than once per N minutes) on the insights endpoint specifically.

## 4. Observability and logging

**Gap:** There is exactly one logger call in the entire codebase (`app/insights.py`, `logger.error` on an Anthropic API failure). There is no logging on expense creation, approval, or rejection; no request/response logging; no correlation/request IDs; no metrics (request counts, latencies, error rates); no tracing. Uvicorn's own access log is the only thing recording that requests happened at all, and it isn't configured for structured output.

**Classification:** Blocking. Without this, an incident (a spike in 409s, a bad approval, a DB error) is invisible until a user reports it, and there's no way to reconstruct what happened.

**Effort:** 2–3 days for a solid baseline: structured logging (e.g. `structlog` or stdlib `logging` with a JSON formatter), a request-ID middleware, log lines on every state transition in `_decide()` (`app/routes.py`), and basic request metrics.

## 5. Error handling

**Gap:** There is no global exception handler registered on the `FastAPI` app (`app/main.py`) — any unhandled exception (a `SQLAlchemyError` from a DB hiccup, a bug in `_decide()`) falls through to Starlette's default 500 handler, whose response shape is undocumented and untested here. The Anthropic API call in `app/insights.py` has no explicit timeout, so a hung call blocks that worker indefinitely (the Streamlit UI compensates with a 60s *client-side* timeout, but that doesn't free the server-side worker). The 404/409 paths that do exist (`app/routes.py`) are well-handled — this gap is specifically about the *unhandled* paths.

**Classification:** Blocking. An unhandled 500 with no consistent shape and no timeout on an external call are both incident-generators, not edge cases.

**Effort:** About 1 day — a global exception handler with a consistent error envelope, plus an explicit `timeout=` on the `anthropic.Anthropic()` client call.

## 6. Database migrations and pooling

**Gap:** `init_db()` (`app/db.py`) calls `Base.metadata.create_all(bind=engine)` at import time in `app/main.py` — this only ever *adds missing tables*, it never alters existing ones. There is no migration tool (e.g. Alembic) in the repo, so any future column/constraint change (adding an FX-history table, a new status, an index) has no upgrade path from an existing database. Separately, `create_engine` in `app/db.py` passes only `connect_args={"check_same_thread": False}` — no explicit `pool_size`, `max_overflow`, `pool_timeout`, or `pool_recycle` are set, so pooling behavior is whatever SQLAlchemy's defaults happen to be for the configured `DATABASE_URL`, untested under concurrent load. This compounds with SQLite's own single-writer limitation, already flagged in `docs/HANDOFF.md`.

**Classification:** Blocking on migrations before any schema change ships to a database with real data in it. Pooling is deferrable while still on SQLite (per HANDOFF.md, SQLite itself is the bottleneck regardless of pool tuning), but must be addressed at the same time as any move to a networked database (e.g. Postgres).

**Effort:** Migrations: 1–2 days to introduce Alembic and generate a baseline migration matching the current schema. Pooling: a few hours once a real DB is in place — mostly picking sane `pool_size`/`pool_recycle` values for the target environment.

## 7. Secrets management

**Gap:** `ANTHROPIC_API_KEY` lives in a plaintext `.env` file loaded via `python-dotenv` (`app/main.py`, `app/db.py`, `app/insights.py`) — reasonable for local dev, but there is **no `.gitignore` in this repo at all**. Right now `.env` is untracked but not ignored, meaning a routine `git add -A` or `git add .` would stage the real API key (and `expenseflow.db`, which may contain real expense data) directly into version control with no warning. There is no integration with a real secret manager (Vault, AWS/GCP Secrets Manager, etc.) for any deployment target.

**Classification:** Blocking — the missing `.gitignore` is an immediate, live risk (not a hypothetical production concern) and should be fixed regardless of anything else in this audit. Moving to a managed secret store is blocking before any real deployment, since a `.env` file on a server's disk is a standing credential-exposure risk (backups, shared hosts, misconfigured file permissions).

**Effort:** `.gitignore` covering `.env`, `.venv/`, `expenseflow.db`, `__pycache__/`: minutes. Secret-manager integration: 0.5–2 days depending on target platform.

## 8. Tests and coverage

**Gap:** There are no test files in the repository — no `test_*.py`, no `conftest.py`. `pytest` is installed and `CLAUDE.md`/`README.md` document `python -m pytest -q` as the test command, but running it today reports zero tests collected. There is no coverage of the one nontrivial concurrency guarantee the code does provide (the atomic `UPDATE ... WHERE status = 'submitted'` in `_decide()`), no coverage of the validation rules in `app/schemas.py`, and no coverage of the 404/409 error paths.

**Classification:** Blocking. Shipping any change to `_decide()`, the schemas, or the FX placeholder without tests risks silently breaking the one correctness guarantee (no double-approval) the system currently has.

**Effort:** 3–5+ days for a first meaningful baseline: unit tests for schema validation, integration tests for all five endpoints against a test DB, a concurrency test for the atomic approve/reject transition, and a fixture/mock for the Anthropic call in `app/insights.py`. Ongoing maintenance after that as endpoints change.

## 9. Deployment and health checks

**Gap:** There is no health-check endpoint (`/health`, `/healthz`, or similar) for a load balancer or orchestrator to poll — `app/routes.py` has no such route. There is no Dockerfile, process-manager config, or deployment manifest anywhere in the repo. Table creation via `init_db()` runs unconditionally at import time with no retry/backoff if the configured database isn't reachable yet, and no graceful-shutdown handling beyond Uvicorn's defaults.

**Classification:** Blocking. Most container orchestrators and load balancers require a health endpoint to route traffic safely, and "no deployment artifact exists" is itself a blocker for any real rollout.

**Effort:** Health endpoint: a few hours. Containerization + deployment config (Dockerfile, startup/readiness wiring, retry-on-DB-unavailable): 1–3 days depending on target platform (bare VM vs. Kubernetes vs. managed PaaS).

## 10. Data privacy for expense data

**Gap:** `GET /expenses` and `GET /expenses/{id}` (`app/routes.py`) return every field on every matching row — including `submitted_by`, `decided_by`, and the free-text `description` — to any caller, with no field-level restriction and no access control (this is the same root cause as §1, but the privacy consequence is distinct: today, anyone who can reach the API can read everyone's expense history, including who submitted and who decided each one). On the positive side, `app/insights.py` only sends `amount_base_minor`, `category`, and `status` to the Anthropic API — not `description`, `submitted_by`, or `decided_by` — so the one place this data leaves the system already minimizes what's shared, which is worth preserving in any future change to that endpoint. There is no encryption at rest (`expenseflow.db` is a plain SQLite file, readable by anything with filesystem access matching the process's permissions), no data-retention policy, and no mechanism to export or delete a given person's records on request.

**Classification:** Blocking if the data behind this ever represents real employees' names and spending (which is the system's entire stated purpose) — unrestricted read access to who-spent-what-on-what is a real privacy exposure, not a theoretical one. The broader retention/export tooling is deferrable until required by an applicable policy or regulation, but access control and at-rest file permissions are not.

**Effort:** Access control itself is covered by the auth work in §1. At-rest file permissions / disk encryption for the SQLite file: a few hours. A full retention/export/deletion program: several days, and ongoing as a policy matter rather than a one-time fix.
