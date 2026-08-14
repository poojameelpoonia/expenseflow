"""Streamlit UI for ExpenseFlow: submit expenses, review and act on them, and generate spending insights."""

import os

import httpx
import streamlit as st

API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")

STATUS_LABELS = {
    "submitted": "Pending",
    "approved": "Approved",
    "rejected": "Rejected",
}

STATUS_ICONS = {
    "Pending": "⏳",
    "Approved": "✅",
    "Rejected": "❌",
}

st.set_page_config(page_title="ExpenseFlow", page_icon="\U0001f4b8", layout="wide")
st.title("\U0001f4b8 ExpenseFlow")
st.caption("Submit expenses, review and act on them, and generate quick spending insights.")


def _show_connection_error() -> None:
    """Render a friendly message instead of a stack trace when the API is unreachable."""
    st.error(f"Could not reach the ExpenseFlow API at {API_BASE}. Is it running?")


def _show_api_error(response: httpx.Response) -> None:
    """Render the API's error detail, if any, without crashing the page."""
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    st.error(f"Request failed ({response.status_code}): {detail}")


def _format_rupees(amount_minor: int) -> str:
    """Format an integer minor-unit amount (paise) as rupees with two decimals, for display only."""
    return f"₹{amount_minor / 100:,.2f}"


def _status_label(raw_status: str) -> str:
    """Map the API's raw status to an icon-prefixed label, so status is never colour-only."""
    label = STATUS_LABELS.get(raw_status, raw_status)
    return f"{STATUS_ICONS.get(label, '')} {label}".strip()


def _fetch_expenses() -> list[dict] | None:
    """Fetch all expenses from the API. Returns None (after reporting the error) on failure."""
    try:
        response = httpx.get(f"{API_BASE}/expenses", timeout=10)
    except httpx.RequestError:
        _show_connection_error()
        return None
    if response.status_code != 200:
        _show_api_error(response)
        return None
    return response.json()


if "submitting" not in st.session_state:
    st.session_state.submitting = False

tab_submit, tab_expenses, tab_insights = st.tabs(["\U0001f4dd Submit", "\U0001f4c4 Expenses", "\U0001f4a1 Insights"])

with tab_submit:
    st.subheader("Submit a new expense")
    with st.form("submit_expense", clear_on_submit=True):
        description = st.text_input("Description")
        amount = st.number_input("Amount", min_value=0.01, step=0.01, format="%.2f")
        currency = st.text_input("Currency", value="INR", max_chars=3)
        category = st.text_input("Category (optional)")
        submitted_by = st.text_input("Submitted by")
        submit_clicked = st.form_submit_button("Submit expense", disabled=st.session_state.submitting)

    if submit_clicked:
        st.session_state.submitting = True
        st.session_state.pending_payload = {
            "description": description,
            "amount_minor": round(amount * 100),
            "currency": currency,
            "category": category or None,
            "submitted_by": submitted_by,
        }
        st.rerun()

    if st.session_state.submitting:
        payload = st.session_state.pop("pending_payload")
        try:
            with st.spinner("Submitting expense..."):
                response = httpx.post(f"{API_BASE}/expenses", json=payload, timeout=10)
            if response.status_code == 201:
                st.success("Expense submitted.")
            else:
                _show_api_error(response)
        except httpx.RequestError:
            _show_connection_error()
        finally:
            st.session_state.submitting = False

with tab_expenses:
    st.subheader("Review expenses")
    all_expenses = _fetch_expenses()

    if all_expenses is not None:
        pending = [e for e in all_expenses if e["status"] == "submitted"]
        approved = [e for e in all_expenses if e["status"] == "approved"]
        rejected = [e for e in all_expenses if e["status"] == "rejected"]

        tile1, tile2, tile3, tile4 = st.columns(4)
        tile1.metric("Total expenses", len(all_expenses))
        tile2.metric("Pending", len(pending))
        tile3.metric("Approved (₹)", _format_rupees(sum(e["amount_base_minor"] for e in approved)))
        tile4.metric("Rejected", len(rejected))

        st.divider()
        st.text_input(
            "Reviewer name",
            key="reviewer",
            help="Used as the 'decided by' name when you approve or reject an expense.",
        )

        filter_cols = st.columns(2)
        status_choice = filter_cols[0].selectbox("Status", ["All", "Pending", "Approved", "Rejected"])
        category_choice = filter_cols[1].text_input("Category contains")

        status_by_label = {"Pending": "submitted", "Approved": "approved", "Rejected": "rejected"}
        filtered = all_expenses
        if status_choice != "All":
            filtered = [e for e in filtered if e["status"] == status_by_label[status_choice]]
        if category_choice:
            filtered = [e for e in filtered if category_choice.lower() in (e["category"] or "").lower()]

        if not filtered:
            st.info("No expenses match these filters.")
        else:
            header_cols = st.columns([1, 3, 2, 2, 2, 2, 2, 1, 1])
            for col, heading in zip(
                header_cols,
                ["ID", "Description", "Amount", "Category", "Submitted by", "Amount (INR)", "Status", "", ""],
            ):
                col.markdown(f"**{heading}**")

            for expense in filtered:
                row_cols = st.columns([1, 3, 2, 2, 2, 2, 2, 1, 1])
                row_cols[0].write(expense["id"])
                row_cols[1].write(expense["description"])
                row_cols[2].write(_format_rupees(expense["amount_minor"]))
                row_cols[3].write(expense["category"] or "—")
                row_cols[4].write(expense["submitted_by"])
                row_cols[5].write(_format_rupees(expense["amount_base_minor"]))
                row_cols[6].write(_status_label(expense["status"]))

                if expense["status"] != "submitted":
                    row_cols[7].write("—")
                    row_cols[8].write("—")
                    continue

                approve_clicked = row_cols[7].button(
                    "Approve", key=f"approve_{expense['id']}", disabled=not st.session_state.reviewer
                )
                reject_clicked = row_cols[8].button(
                    "Reject", key=f"reject_{expense['id']}", disabled=not st.session_state.reviewer
                )

                if approve_clicked or reject_clicked:
                    action = "approve" if approve_clicked else "reject"
                    try:
                        decision_response = httpx.post(
                            f"{API_BASE}/expenses/{expense['id']}/{action}",
                            json={"decided_by": st.session_state.reviewer},
                            timeout=10,
                        )
                        if decision_response.status_code == 200:
                            st.toast(f"Expense #{expense['id']} {action}d.", icon="✅")
                            st.rerun()
                        else:
                            _show_api_error(decision_response)
                    except httpx.RequestError:
                        _show_connection_error()

with tab_insights:
    st.subheader("Spending insights")
    if st.button("Generate insights"):
        try:
            with st.spinner("Generating insights..."):
                response = httpx.get(f"{API_BASE}/reports/insights", timeout=60)
            if response.status_code == 200:
                st.markdown(response.json().get("insight", ""))
            else:
                _show_api_error(response)
        except httpx.RequestError:
            _show_connection_error()
