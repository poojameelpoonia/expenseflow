# ExpenseFlow — Architecture

PoC expense submission and approval API. One journey: submit an expense (converted to INR at submission time via a live FX lookup), then approve or reject it. Scope and conventions are fixed by `CLAUDE.md`; this document is the agreed design before implementation.

## 1. Schema — `expenses` table

| Column | Type | Why it exists |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` (autoincrement) | Surrogate key used in `GET /expenses/{id}` and the approve/reject URLs. |
| `description` | `VARCHAR NOT NULL` | What the money was for — the thing an approver actually judges. |
| `category` | `VARCHAR NULL` | Optional free-text label; not load-bearing for the approve/reject flow, so nullable rather than a constrained enum/table. |
| `original_amount_minor` | `INTEGER NOT NULL, CHECK (original_amount_minor > 0)` | The claim exactly as submitted, in minor units of the original currency — kept separate from the normalised value so the original input is always reconstructable. |
| `original_currency` | `VARCHAR(3) NOT NULL` | ISO-4217 code needed to interpret `original_amount_minor` and to know what conversion ran. |
| `amount_inr_minor` | `INTEGER NOT NULL, CHECK (amount_inr_minor >= 0)` | The normalised base-currency (INR) value in paise — what "normalised to base on write" produces and what approvers reason about. |
| `fx_rate` | `VARCHAR NOT NULL` | Exact rate used, stored as the decimal **string** returned by the FX API — never a float, never a lossy `NUMERIC` — so the conversion is reproducible later. |
| `fx_rate_fetched_at` | `DATETIME NOT NULL` | When the rate was fetched; kept distinct from `created_at` for audit precision. |
| `fx_source` | `VARCHAR NULL` | Which provider supplied the rate. Optional, cheap, strengthens the audit trail. |
| `status` | `VARCHAR NOT NULL, CHECK (status IN ('submitted','approved','rejected')), DEFAULT 'submitted'` | Closed three-value set, enforced via a Python `Enum` mapped through SQLAlchemy's `Enum` type (emits the CHECK automatically). |
| `submitted_by` | `VARCHAR NOT NULL` | Caller-supplied identifier — there is no auth system, so this is an unauthenticated free-text field, not a user FK. |
| `decided_by` | `VARCHAR NULL` | Same idea, filled in at approve/reject time; null while `status = 'submitted'`. |
| `created_at` | `DATETIME NOT NULL, DEFAULT now` | Submission timestamp. |
| `updated_at` | `DATETIME NOT NULL, DEFAULT now, ON UPDATE now` | Generic last-modified marker. |
| `decided_at` | `DATETIME NULL` | When approve/reject happened; null until decided. |

**Design decisions (not left open):**
- **No FX-rate-history or status-history table.** Each expense goes through exactly one conversion and at most one decision — no re-pricing, no multi-stage approval. Every auditable fact fits as scalar columns on one row; a history table would be normalising for change that can't happen in this journey.
- **`status` is an enum + CHECK, not a lookup table.** Three fixed values known at code time don't warrant a foreign-key relationship.
- **No `rejection_reason` / decision-note field.** Not part of the brief's single journey; adding it would be inventing scope beyond "approve or reject."
- **Currency scope: fixed allowlist of 2-decimal-minor-unit currencies** (e.g. INR, USD, EUR, GBP), enforced in `schemas.py`. Zero-decimal currencies (JPY, KWD) would need a currency-exponent lookup table to convert minor units correctly — out of scope for the PoC; expand the allowlist later if needed.

## 2. Endpoints

| Method | Path | Request body | Response |
|---|---|---|---|
| `POST` | `/expenses` | `{description, category?, original_amount_minor, original_currency, submitted_by}` | `201` full record incl. computed `amount_inr_minor`, `fx_rate`, `fx_rate_fetched_at`, `status="submitted"`. `502` if the FX lookup fails (see edge case 1). |
| `GET` | `/expenses` | — (optional `?status=` filter) | `200` list of records. |
| `GET` | `/expenses/{id}` | — | `200` record, `404` if missing. |
| `POST` | `/expenses/{id}/approve` | `{decided_by}` | `200` updated record (`status="approved"`, `decided_at` set); `404` missing; `409` invalid transition. |
| `POST` | `/expenses/{id}/reject` | `{decided_by}` | Same shape, `status="rejected"`. |

`GET /expenses` (with the status filter) is the minimal read path implied by needing something to approve/reject — not a new feature beyond the brief.

**Why actions (`POST .../approve`) instead of `PATCH /expenses/{id}` with a status field:** a generic PATCH would need its own logic to whitelist which field can change, to which values, and from which prior state — i.e. it reimplements the same transition rules behind a more permissive-looking interface, and risks other fields (amount, currency) being editable post-submission. Dedicated action endpoints make the only two legal operations explicit in the API surface and structurally block editing anything else.

## 3. File layout

- **`app/db.py`** — SQLAlchemy engine (`sqlite:///expenseflow.db`, path read from env via `python-dotenv` with that path as fallback), `SessionLocal`, `Base` (2.0-style `DeclarativeBase`), and the `get_db()` FastAPI dependency.
- **`app/models.py`** — `ExpenseStatus` enum and the single `Expense(Base)` ORM class covering every column above, with the two `CHECK` constraints in `__table_args__`.
- **`app/schemas.py`** — Pydantic v2 models: `ExpenseCreate` (request for `POST /expenses`, with `original_amount_minor: int = Field(gt=0)` and `original_currency` restricted to the allowlist), `ExpenseDecision` (request for approve/reject), `ExpenseRead` (response, `model_config = ConfigDict(from_attributes=True)`). Imports `ExpenseStatus` from `models.py`; no business logic.
- **`app/routes.py`** — `APIRouter` with all five endpoint functions (typed, docstringed, `Depends(get_db)`), plus the FX-rate-fetching `httpx` call and the `Decimal` conversion math as a plain top-level helper function, grouped separately from the endpoint bodies under a comment banner. There's no `services.py` in the allowed layout, so this is the only place that logic can live; env vars for the FX endpoint are read lazily inside the helper via `os.getenv(...)`, not as module-level constants, to avoid an import-order race with `load_dotenv()`.
- **`app/main.py`** — calls `load_dotenv()` first, instantiates `FastAPI()`, includes the router, and calls `Base.metadata.create_all(bind=engine)` at import time for table creation. Wiring only, no route logic.

## 4. Edge cases

**1. External FX API is down, times out, or returns a bad rate.**
Decision: **fail the whole submission**, no fallback rate, no silent default, no retry loop. Persisting an expense without a genuinely fetched rate would break the "normalised to base currency on write" invariant and make the audit columns lie about what happened. The ORM row is only constructed after the rate is fetched and validated (positive, parseable `Decimal`); any `httpx` timeout/HTTP error/bad payload is caught in the endpoint and returned as `502`, with nothing written to the DB. A short explicit timeout (5s) is used; retry is left to the client resubmitting. If `original_currency == "INR"`, the external call is skipped entirely (`fx_rate="1"`, `fx_rate_fetched_at=now`) since no conversion is needed.

**2. Invalid state transitions — double approve, approve-after-reject.**
The CHECK constraint only blocks garbage *values*, not illegal *transitions* — that's application logic in `routes.py`. Decision: **strict** — any call to approve/reject on an expense that isn't currently `submitted` returns `409 Conflict`, including a repeat call on an already-approved expense (no idempotent no-op). This is simpler to reason about and test than treating same-state repeats as a no-op, and for an irreversible financial action, a second call is treated as a bug signal rather than silently accepted. The state check-and-set is done as a single atomic `UPDATE ... WHERE id = :id AND status = 'submitted'`, inspecting `rowcount`, rather than `SELECT` then `UPDATE`, to avoid a race between two concurrent approve/reject calls.

**3. Rounding/precision converting minor units across a fractional FX rate.**
`original_amount_minor * fx_rate` is essentially never an integer. Decision: all arithmetic uses Python `Decimal` — the stored `fx_rate` string is parsed back into a `Decimal` (never a float) — then quantized to the nearest paisa with `ROUND_HALF_UP` before casting to `int` for `amount_inr_minor`. `ROUND_HALF_UP` matches what a human auditor expects, versus banker's rounding. Because the rate is stored as the exact decimal string and the rounding rule is fixed, anyone auditing later can recompute `Decimal(original_amount_minor) * Decimal(fx_rate)` with the same quantization and get back exactly the stored `amount_inr_minor`. This scheme relies on both currencies having 2 decimal places, which is guaranteed by the currency allowlist decision above.

## Notes for implementation

- FX provider and test strategy: use a live httpx call to an FX rate API at submission time; `pytest` mocks the httpx call (via `respx` is not available — use `unittest.mock`/monkeypatch, since no new dependency may be added) rather than hitting the real API in tests.
- `submitted_by` / `decided_by` are unauthenticated free-text strings — there is no auth system in this PoC.
- Table creation uses `Base.metadata.create_all()` at import time in `main.py` rather than a `lifespan` hook — simplest option consistent with "PoC, not production."
