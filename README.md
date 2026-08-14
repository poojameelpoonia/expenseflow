# ExpenseFlow

A small expense submission and approval API. This is a proof of concept, not a production service.

One user journey: submit an expense, have it normalised to a base currency (INR), then approve or reject it. A Streamlit UI (`ui/app.py`) and an AI-generated spending-insights endpoint are also included.

> **Note on FX conversion:** `POST /expenses` does not yet call a live FX rate provider. `app/routes.py` currently converts every submission 1:1 into the base currency (`amount_base_minor = amount_minor`, `fx_rate` is stored as `"1"`, `fx_source` is `null`) — see the `TODO` comment in that file. Live FX lookup is planned but not implemented.

## What it does

- **Submit an expense** — description, amount (integer minor units), currency, optional category, submitter name. Stored with status `submitted`.
- **List / fetch expenses** — all expenses, optionally filtered by status and/or category, or a single expense by id.
- **Approve or reject** — moves a `submitted` expense to `approved` or `rejected`, atomically, recording who decided and when. Only valid from `submitted`; re-approving/rejecting or acting on a missing expense is rejected.
- **Spending insights** — `GET /reports/insights` sends a summary of all expenses to the Anthropic API and returns three bullet points of generated insight (with a safe fallback string if the call fails).
- **Streamlit UI** (`ui/app.py`) — a browser front end over the API with tabs to submit expenses, review/approve/reject them, and generate insights. Optional; the API works standalone.

## Stack

- Python 3.10+ (this repo's `.venv` is built with 3.10.12)
- FastAPI + Uvicorn
- SQLAlchemy ORM on SQLite (`expenseflow.db` by default)
- httpx (used by the Streamlit UI to call the API; also present for the planned FX call)
- pydantic v2 for request/response models
- pytest for tests
- anthropic SDK (spending insights)
- streamlit (optional UI)

Money is always stored as integer minor units (paise), never float. Base currency is INR.

There is no `requirements.txt`/`pyproject.toml` in this repo yet, so dependencies are installed directly (see below).

## Setup on Windows

From the project root, in PowerShell or cmd.exe:

```powershell
# Create the virtual environment
py -3.12 -m venv .venv

# Activate it
.venv\Scripts\Activate.ps1        # PowerShell
.venv\Scripts\activate.bat        # cmd.exe

# Install dependencies
pip install fastapi "uvicorn[standard]" sqlalchemy httpx "pydantic>=2" python-dotenv pytest anthropic streamlit
```

`uvicorn[standard]` pulls in `watchfiles` so `--reload` works properly.

## Configure `.env`

Create a `.env` file in the project root (never commit it — secrets are read via `python-dotenv`, not hardcoded):

```
ANTHROPIC_API_KEY=your-anthropic-api-key
DATABASE_URL=sqlite:///expenseflow.db
API_BASE=http://127.0.0.1:8000
```

- `ANTHROPIC_API_KEY` — required only for `GET /reports/insights` (`app/insights.py` constructs `anthropic.Anthropic()` from the environment). Without it, that one endpoint returns the fallback insight text.
- `DATABASE_URL` — optional, read in `app/db.py`; defaults to `sqlite:///expenseflow.db` if unset.
- `API_BASE` — optional, read by the Streamlit UI (`ui/app.py`) to know where the API is running; defaults to `http://127.0.0.1:8000` if unset. Not read by the API itself.

## Run

Start the API (creates `expenseflow.db` and its tables on first run, via `init_db()` in `app/main.py`):

```powershell
python -m uvicorn app.main:app --reload
```

The API is then at `http://127.0.0.1:8000` (interactive docs at `/docs`).

Optionally, start the Streamlit UI in a second terminal (with the API already running):

```powershell
streamlit run ui/app.py
```

## Test

```powershell
python -m pytest -q
```

No test files exist in the repo yet — this command currently reports "no tests ran."

## Endpoint reference

### `POST /expenses`

Submit a new expense. Status `201` on success.

Request body:

| Field | Type | Notes |
|---|---|---|
| `description` | `string` | required |
| `amount_minor` | `int` | required, must be `> 0` |
| `currency` | `string` | required, exactly 3 letters; normalised to uppercase |
| `category` | `string \| null` | optional |
| `submitted_by` | `string` | required |

Response body (`ExpenseOut`):

| Field | Type | Notes |
|---|---|---|
| `id` | `int` | |
| `description` | `string` | |
| `amount_minor` | `int` | original submitted amount |
| `currency` | `string` | original currency, uppercased |
| `category` | `string \| null` | |
| `submitted_by` | `string` | |
| `amount_base_minor` | `int` | normalised INR amount (currently 1:1 with `amount_minor` — see FX note above) |
| `status` | `"submitted" \| "approved" \| "rejected"` | always `"submitted"` on creation |
| `created_at` | `datetime` | |

### `GET /expenses`

List expenses. Status `200`, returns a list of `ExpenseOut`.

Query parameters (both optional, combinable):

| Param | Type | Notes |
|---|---|---|
| `status` | `"submitted" \| "approved" \| "rejected"` | filter by exact status |
| `category` | `string` | filter by exact category match |

### `GET /expenses/{expense_id}`

Fetch a single expense.

- `200` — `ExpenseOut`
- `404` — expense does not exist

### `POST /expenses/{expense_id}/approve`

Approve a submitted expense.

Request body: `{"decided_by": "string"}`

- `200` — `ExpenseOut` with `status="approved"`
- `404` — expense does not exist
- `409` — expense is not currently `submitted` (includes its current status in the error detail)

### `POST /expenses/{expense_id}/reject`

Same as approve, but sets `status="rejected"`.

Request body: `{"decided_by": "string"}`

- `200` — `ExpenseOut` with `status="rejected"`
- `404` — expense does not exist
- `409` — expense is not currently `submitted`

### `GET /reports/insights`

Generate a short spending-insight summary across all expenses.

- `200` — `{"insight": "string"}`, three bullet points generated from the current set of expenses, or a fallback message if the Anthropic API call fails
