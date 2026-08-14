# 1. Store money as integer minor units

## Status

Accepted

## Context

ExpenseFlow stores an expense's original amount and its INR-normalised amount, and later has to compare, sum, and round these values (e.g. converting via an FX rate, aggregating approved totals in the UI). `CLAUDE.md` fixes the rule up front: "Money is stored as integer minor units (paise / cents), never float." This ADR records why.

The core problem is that IEEE-754 binary floats cannot represent most decimal fractions (including `0.10`) exactly, so repeated arithmetic on float currency values accumulates rounding error — a well-known source of off-by-a-paisa bugs and failed audits in financial systems.

## Decision

Store every amount as an `int` in the smallest unit of its currency (paise for INR, cents for USD, etc.) — see `original_amount_minor` and `amount_inr_minor` in `app/models.py`, both plain SQLite `INTEGER` columns with `CHECK` constraints (`> 0` and `>= 0` respectively). Pydantic request/response models (`app/schemas.py`) mirror this as `int` fields (`amount_minor`, `amount_base_minor`), constrained with `gt=0` on input. Display formatting (e.g. dividing by 100 for a `₹`-prefixed string in `ui/app.py`) happens only at the UI layer, never in storage or in the API contract. The FX rate itself is stored as a `String` (`fx_rate` in `app/models.py`), not a float, so it can be parsed back into a `Decimal` for reproducible conversion math rather than losing precision at rest.

## Alternatives considered

- **Float amounts (e.g. `123.45`).** Rejected: binary floating point can't represent most decimal fractions exactly, so sums and FX conversions drift by fractions of a paisa in ways that are hard to reproduce or explain to an auditor.
- **Decimal/Numeric column type.** Workable in principle (arbitrary-precision, no binary rounding error), but SQLite has no native fixed-precision numeric type — `NUMERIC` in SQLite still round-trips through a dynamic type affinity that can silently become a float. Plain integers avoid relying on that affinity behavior entirely, and keep the column type unambiguous across any future move to a different database engine.
- **String amounts (e.g. `"123.45"`).** Rejected: pushes decimal parsing and arithmetic into every consumer (API clients, the UI), with no enforcement that the string is even well-formed money; integers get free validation (`CHECK (amount > 0)`) and free arithmetic.

## Consequences

- All arithmetic on amounts (summing, comparing, eventually converting via a real FX rate) is exact integer/Decimal arithmetic — no float rounding error, and results are reproducible by anyone re-deriving them later.
- Every layer that touches an amount must remember the "minor units" convention — there is no type-level distinction between "100 rupees" and "100 paise." `ui/app.py`'s `_format_rupees` divides by 100 for display, and the Streamlit submit form multiplies a decimal rupee input back into minor units (`round(amount * 100)`) — both of those conversions are places a future change could silently reintroduce a units bug if not kept in sync.
- The scheme assumes every supported currency has exactly 2 decimal places (matches `docs/ARCHITECTURE.md`'s currency-allowlist decision). Zero-decimal currencies (JPY, KWD) or 3-decimal currencies (BHD) would convert incorrectly under a flat "divide/multiply by 100" rule and would need an explicit per-currency exponent lookup before being added.
- The API contract itself is minor-units-in, minor-units-out (`amount_minor` on request, `amount_minor`/`amount_base_minor` on response) — API consumers must do their own major/minor conversion for display, matching what the Streamlit UI already does. This is a deliberate boundary: the API never guesses a display format on a caller's behalf.
