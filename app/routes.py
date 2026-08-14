"""API routes for submitting, listing, and deciding on expenses."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.insights import generate_insight
from app.models import Expense, ExpenseStatus
from app.schemas import ExpenseCreate, ExpenseDecision, ExpenseOut

router = APIRouter()


@router.post("/expenses", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
def create_expense(payload: ExpenseCreate, db: Session = Depends(get_db)) -> Expense:
    """Submit a new expense with status "submitted"."""
    # TODO: FX conversion goes here — fetch a live rate for payload.currency -> INR,
    # convert with Decimal + ROUND_HALF_UP, and store the real rate/source/fetched_at
    # instead of this 1:1 placeholder (see docs/ARCHITECTURE.md sections 2 and 4.1/4.3).
    amount_base_minor = payload.amount_minor
    expense = Expense(
        description=payload.description,
        category=payload.category,
        original_amount_minor=payload.amount_minor,
        original_currency=payload.currency,
        amount_inr_minor=amount_base_minor,
        fx_rate="1",
        fx_rate_fetched_at=datetime.utcnow(),
        fx_source=None,
        submitted_by=payload.submitted_by,
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.get("/expenses", response_model=list[ExpenseOut])
def list_expenses(
    status_: ExpenseStatus | None = Query(default=None, alias="status"),
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Expense]:
    """List expenses, optionally filtered by status and/or category."""
    stmt = select(Expense)
    if status_ is not None:
        stmt = stmt.where(Expense.status == status_)
    if category is not None:
        stmt = stmt.where(Expense.category == category)
    return list(db.execute(stmt).scalars().all())


@router.get("/expenses/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Fetch a single expense by id, or 404 if it doesn't exist."""
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    return expense


def _decide(db: Session, expense_id: int, new_status: ExpenseStatus, decided_by: str) -> Expense:
    """Atomically move a submitted expense to `new_status`, or raise 404/409."""
    result = db.execute(
        update(Expense)
        .where(Expense.id == expense_id, Expense.status == ExpenseStatus.SUBMITTED)
        .values(status=new_status, decided_by=decided_by, decided_at=datetime.utcnow())
    )
    db.commit()

    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Expense is not in submitted state (current status: {expense.status.value})",
        )
    return expense


@router.post("/expenses/{expense_id}/approve", response_model=ExpenseOut)
def approve_expense(expense_id: int, payload: ExpenseDecision, db: Session = Depends(get_db)) -> Expense:
    """Approve a submitted expense. 404 if missing, 409 if not currently submitted."""
    return _decide(db, expense_id, ExpenseStatus.APPROVED, payload.decided_by)


@router.post("/expenses/{expense_id}/reject", response_model=ExpenseOut)
def reject_expense(expense_id: int, payload: ExpenseDecision, db: Session = Depends(get_db)) -> Expense:
    """Reject a submitted expense. 404 if missing, 409 if not currently submitted."""
    return _decide(db, expense_id, ExpenseStatus.REJECTED, payload.decided_by)


@router.get("/reports/insights")
def get_insights(db: Session = Depends(get_db)) -> dict:
    """Generate a short spending insight summary across all expenses."""
    expenses = list(db.execute(select(Expense)).scalars().all())
    expense_dicts = [
        {
            "amount_base_minor": expense.amount_inr_minor,
            "category": expense.category,
            "status": expense.status.value,
        }
        for expense in expenses
    ]
    return {"insight": generate_insight(expense_dicts)}
