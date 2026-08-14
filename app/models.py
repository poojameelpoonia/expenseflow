"""ORM models for ExpenseFlow."""

import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExpenseStatus(str, enum.Enum):
    """Lifecycle states for an expense."""

    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"


class Expense(Base):
    """An expense claim, its INR-normalised amount, and its approval state.

    `status` is mapped through SQLAlchemy's `Enum` type with
    `create_constraint=True`, so on SQLite (which has no native enum type)
    this compiles to a `VARCHAR` column plus a
    `CHECK (status IN ('submitted', 'approved', 'rejected'))` constraint —
    invalid status strings are rejected by the database itself without a
    separate explicit CheckConstraint. `values_callable` is required
    alongside it so the constraint and stored strings use the enum
    *values* (`"submitted"`) rather than SQLAlchemy's default of the
    member *names* (`"SUBMITTED"`).
    """

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("original_amount_minor > 0", name="ck_expenses_original_amount_minor_positive"),
        CheckConstraint("amount_inr_minor >= 0", name="ck_expenses_amount_inr_minor_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    description: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    original_amount_minor: Mapped[int] = mapped_column(nullable=False)
    original_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_inr_minor: Mapped[int] = mapped_column(nullable=False)
    fx_rate: Mapped[str] = mapped_column(String, nullable=False)
    fx_rate_fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fx_source: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[ExpenseStatus] = mapped_column(
        Enum(
            ExpenseStatus,
            name="expense_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
        default=ExpenseStatus.SUBMITTED,
        server_default=ExpenseStatus.SUBMITTED.value,
    )
    submitted_by: Mapped[str] = mapped_column(String, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
