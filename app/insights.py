"""Spending insights for a set of expenses, generated via the Anthropic Messages API."""

import logging

import anthropic
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_FALLBACK_INSIGHT = "Insights are temporarily unavailable. Please try again later."


def _build_summary(expenses: list[dict]) -> str:
    """Render expenses as a compact one-line-per-expense text block."""
    lines = [
        f"- {expense.get('amount_base_minor')} paise, "
        f"category={expense.get('category')}, "
        f"status={expense.get('status')}"
        for expense in expenses
    ]
    return "\n".join(lines)


def generate_insight(expenses: list[dict]) -> str:
    """Summarise spending patterns across a list of expenses.

    Args:
        expenses: Expense records as dicts with `amount_base_minor` (int,
            INR paise), `category` (str | None), and `status` (str) keys.

    Returns:
        Three short bullet points of spending insight, or a safe fallback
        string if the underlying API call fails for any reason.
    """
    summary = _build_summary(expenses)
    prompt = (
        "Here is a list of expenses (amounts in INR paise):\n"
        f"{summary}\n\n"
        "Give exactly three short bullet points of insight about this spending."
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return next(block.text for block in response.content if block.type == "text")
    except (anthropic.APIError, anthropic.APIConnectionError) as exc:
        logger.error("Failed to generate spending insight: %s", exc)
        return _FALLBACK_INSIGHT
