"""Core invoice models and validation types."""

import datetime
import hashlib
from decimal import Decimal
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


class ValidationFlag(str, Enum):
    """Flags raised during invoice validation."""

    UNKNOWN_ITEM = "UNKNOWN_ITEM"
    STOCK_EXCEEDED = "STOCK_EXCEEDED"
    ZERO_STOCK = "ZERO_STOCK"
    NEGATIVE_QTY = "NEGATIVE_QTY"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    MISSING_VENDOR = "MISSING_VENDOR"
    MISSING_DUE_DATE = "MISSING_DUE_DATE"
    INVALID_DATE = "INVALID_DATE"
    FUZZY_MATCH = "FUZZY_MATCH"
    FOREIGN_CURRENCY = "FOREIGN_CURRENCY"
    HIGH_VALUE = "HIGH_VALUE"
    FRAUD_SUSPECT = "FRAUD_SUSPECT"
    BLACKLISTED_VENDOR = "BLACKLISTED_VENDOR"


class ApprovalStatus(str, Enum):
    """Possible approval outcomes."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    PENDING = "PENDING"


class Vendor(BaseModel, frozen=True):
    """Vendor information extracted from invoice."""

    name: str = ""
    address: str | None = None

    @property
    def is_empty(self) -> bool:
        """Check if vendor info is missing."""
        return not self.name.strip()


class LineItem(BaseModel, frozen=True):
    """A single line item on an invoice."""

    item: str
    quantity: int
    unit_price: Annotated[Decimal, Field(ge=0)]
    amount: Decimal | None = None  # Optional, can be computed
    note: str | None = None

    # Track fuzzy matching results
    original_item_name: str | None = None  # If fuzzy matched, store original
    confidence: float = 1.0  # Extraction confidence (0-1)

    @property
    def computed_amount(self) -> Decimal:
        """Calculate line total from qty * unit_price."""
        return Decimal(self.quantity) * self.unit_price

    @field_validator("item", mode="before")
    @classmethod
    def normalize_item_name(cls, v: str) -> str:
        """Strip whitespace from item names."""
        if isinstance(v, str):
            return v.strip()
        return v


class Invoice(BaseModel, frozen=True):
    """Structured invoice data extracted from raw document."""

    invoice_number: str
    vendor: Vendor
    date: datetime.date | None = None
    due_date: datetime.date | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    total: Decimal
    currency: str = "USD"
    payment_terms: str | None = None
    notes: str | None = None

    # Extraction metadata
    source_file: str | None = None
    extraction_confidence: float = 1.0

    @field_validator("invoice_number", mode="before")
    @classmethod
    def normalize_invoice_number(cls, v: str) -> str:
        """Normalize invoice number format."""
        if isinstance(v, str):
            v = v.strip()
            # Handle variations like "INV 1012" -> "INV-1012"
            v = v.replace(" ", "-")
            # Add prefix if just a number
            if v.isdigit():
                v = f"INV-{v}"
            elif v.startswith("INV") and not v.startswith("INV-"):
                v = v.replace("INV", "INV-", 1)
        return v

    @property
    def computed_subtotal(self) -> Decimal:
        """Sum of all line item amounts."""
        return sum(
            (item.amount if item.amount is not None else item.computed_amount)
            for item in self.line_items
        )

    @property
    def is_high_value(self) -> bool:
        """Check if invoice exceeds $10K threshold."""
        return self.total >= Decimal("10000")

    def get_aggregated_quantities(self) -> dict[str, int]:
        """Aggregate quantities by item name (for duplicate line items)."""
        totals: dict[str, int] = {}
        for item in self.line_items:
            totals[item.item] = totals.get(item.item, 0) + item.quantity
        return totals


class FlagDetail(BaseModel, frozen=True):
    """Details about a validation flag."""

    flag: ValidationFlag
    message: str
    item: str | None = None  # Related item, if applicable
    severity: str = "error"  # error, warning, info


class ValidationResult(BaseModel, frozen=True):
    """Result of invoice validation against inventory."""

    invoice_number: str
    is_valid: bool
    flags: list[FlagDetail] = Field(default_factory=list)
    validated_items: dict[str, dict] = Field(default_factory=dict)  # item -> {stock, requested}

    @property
    def has_hard_flags(self) -> bool:
        """Check for any hard-reject flags."""
        hard_flags = {
            ValidationFlag.UNKNOWN_ITEM,
            ValidationFlag.STOCK_EXCEEDED,
            ValidationFlag.ZERO_STOCK,
            ValidationFlag.NEGATIVE_QTY,
            ValidationFlag.FRAUD_SUSPECT,
            ValidationFlag.BLACKLISTED_VENDOR,
            ValidationFlag.MISSING_VENDOR,
        }
        return any(f.flag in hard_flags for f in self.flags)

    @property
    def needs_human_review(self) -> bool:
        """Check for flags requiring human intervention."""
        review_flags = {
            ValidationFlag.FOREIGN_CURRENCY,
            ValidationFlag.DUPLICATE_INVOICE,
            ValidationFlag.FUZZY_MATCH,
        }
        return any(f.flag in review_flags for f in self.flags)


class CritiqueResult(BaseModel, frozen=True):
    """Result of the critic reviewing an approval decision."""

    accepted: bool
    reasoning: str
    suggested_changes: str | None = None


class ApprovalDecision(BaseModel, frozen=True):
    """Final approval decision with reasoning chain."""

    invoice_number: str
    status: ApprovalStatus
    reasoning: str
    critique_history: list[CritiqueResult] = Field(default_factory=list)
    rules_applied: list[str] = Field(default_factory=list)
    escalation_reason: str | None = None

    @property
    def effective_escalation_reason(self) -> str | None:
        """Get escalation reason, falling back to reasoning for NEEDS_HUMAN status."""
        if self.status == ApprovalStatus.NEEDS_HUMAN:
            return self.escalation_reason or self.reasoning
        return self.escalation_reason
