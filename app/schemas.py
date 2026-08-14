"""Pydantic v2 request and response schemas for ExpenseFlow."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import ExpenseStatus


class ExpenseCreate(BaseModel):
    """Request body for submitting a new expense."""

    description: str
    amount_minor: int = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    category: str | None = None
    submitted_by: str

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        """Normalise to uppercase and reject anything but a 3-letter alphabetic code."""
        value = value.upper()
        if not value.isalpha():
            raise ValueError("currency must be a 3-letter alphabetic code")
        return value


class ExpenseDecision(BaseModel):
    """Request body for approving or rejecting an expense."""

    decided_by: str


class ExpenseOut(BaseModel):
    """Response body for an expense, including its INR-normalised amount and status."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    description: str
    amount_minor: int = Field(validation_alias="original_amount_minor")
    currency: str = Field(validation_alias="original_currency")
    category: str | None
    submitted_by: str
    amount_base_minor: int = Field(validation_alias="amount_inr_minor")
    status: ExpenseStatus
    created_at: datetime
