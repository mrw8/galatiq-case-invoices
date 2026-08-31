"""Tests for Pydantic models."""

from decimal import Decimal

import pytest

from src.models.invoice import (
    ApprovalDecision,
    ApprovalStatus,
    FlagDetail,
    Invoice,
    LineItem,
    ValidationFlag,
    ValidationResult,
    Vendor,
)


class TestVendor:
    """Tests for Vendor model."""

    def test_vendor_with_name(self) -> None:
        vendor = Vendor(name="Test Corp", address="123 Main St")
        assert vendor.name == "Test Corp"
        assert vendor.address == "123 Main St"
        assert not vendor.is_empty

    def test_vendor_empty(self) -> None:
        vendor = Vendor(name="", address=None)
        assert vendor.is_empty

    def test_vendor_whitespace_only(self) -> None:
        vendor = Vendor(name="   ", address=None)
        assert vendor.is_empty


class TestLineItem:
    """Tests for LineItem model."""

    def test_line_item_computed_amount(self) -> None:
        item = LineItem(item="WidgetA", quantity=10, unit_price=Decimal("250.00"))
        assert item.computed_amount == Decimal("2500.00")

    def test_line_item_normalizes_name(self) -> None:
        item = LineItem(item="  Widget A  ", quantity=5, unit_price=Decimal("100"))
        assert item.item == "Widget A"

    def test_line_item_with_explicit_amount(self) -> None:
        item = LineItem(
            item="WidgetA",
            quantity=10,
            unit_price=Decimal("250.00"),
            amount=Decimal("2400.00"),  # Discounted
        )
        assert item.amount == Decimal("2400.00")
        assert item.computed_amount == Decimal("2500.00")  # Still computes from qty*price


class TestInvoice:
    """Tests for Invoice model."""

    def test_invoice_number_normalization(self) -> None:
        # Just a number
        inv1 = Invoice(
            invoice_number="1001",
            vendor=Vendor(name="Test"),
            total=Decimal("100"),
        )
        assert inv1.invoice_number == "INV-1001"

        # Missing hyphen
        inv2 = Invoice(
            invoice_number="INV1002",
            vendor=Vendor(name="Test"),
            total=Decimal("100"),
        )
        assert inv2.invoice_number == "INV-1002"

        # Space instead of hyphen
        inv3 = Invoice(
            invoice_number="INV 1003",
            vendor=Vendor(name="Test"),
            total=Decimal("100"),
        )
        assert inv3.invoice_number == "INV-1003"

    def test_invoice_is_high_value(self) -> None:
        low_value = Invoice(
            invoice_number="INV-001",
            vendor=Vendor(name="Test"),
            total=Decimal("5000"),
        )
        assert not low_value.is_high_value

        high_value = Invoice(
            invoice_number="INV-002",
            vendor=Vendor(name="Test"),
            total=Decimal("15000"),
        )
        assert high_value.is_high_value

        threshold = Invoice(
            invoice_number="INV-003",
            vendor=Vendor(name="Test"),
            total=Decimal("10000"),
        )
        assert threshold.is_high_value

    def test_invoice_aggregated_quantities(self) -> None:
        invoice = Invoice(
            invoice_number="INV-001",
            vendor=Vendor(name="Test"),
            total=Decimal("1000"),
            line_items=[
                LineItem(item="WidgetA", quantity=5, unit_price=Decimal("100")),
                LineItem(item="WidgetB", quantity=3, unit_price=Decimal("100")),
                LineItem(item="WidgetA", quantity=3, unit_price=Decimal("100")),  # Duplicate
            ],
        )
        aggregated = invoice.get_aggregated_quantities()
        assert aggregated == {"WidgetA": 8, "WidgetB": 3}


class TestValidationResult:
    """Tests for ValidationResult model."""

    def test_has_hard_flags(self) -> None:
        # No flags
        result1 = ValidationResult(invoice_number="INV-001", is_valid=True, flags=[])
        assert not result1.has_hard_flags

        # Soft flag only
        result2 = ValidationResult(
            invoice_number="INV-002",
            is_valid=True,
            flags=[FlagDetail(flag=ValidationFlag.HIGH_VALUE, message="Over 10K")],
        )
        assert not result2.has_hard_flags

        # Hard flag
        result3 = ValidationResult(
            invoice_number="INV-003",
            is_valid=False,
            flags=[FlagDetail(flag=ValidationFlag.STOCK_EXCEEDED, message="No stock")],
        )
        assert result3.has_hard_flags

    def test_needs_human_review(self) -> None:
        result = ValidationResult(
            invoice_number="INV-001",
            is_valid=True,
            flags=[FlagDetail(flag=ValidationFlag.FOREIGN_CURRENCY, message="EUR")],
        )
        assert result.needs_human_review


class TestApprovalDecision:
    """Tests for ApprovalDecision model."""

    def test_effective_escalation_reason_fallback(self) -> None:
        decision = ApprovalDecision(
            invoice_number="INV-001",
            status=ApprovalStatus.NEEDS_HUMAN,
            reasoning="Requires manual review",
        )
        # escalation_reason is None, but effective_escalation_reason falls back
        assert decision.escalation_reason is None
        assert decision.effective_escalation_reason == "Requires manual review"

    def test_effective_escalation_reason_explicit(self) -> None:
        decision = ApprovalDecision(
            invoice_number="INV-001",
            status=ApprovalStatus.NEEDS_HUMAN,
            reasoning="Complex case",
            escalation_reason="Foreign currency needs conversion",
        )
        assert decision.escalation_reason == "Foreign currency needs conversion"
        assert decision.effective_escalation_reason == "Foreign currency needs conversion"

    def test_effective_escalation_reason_non_escalated(self) -> None:
        decision = ApprovalDecision(
            invoice_number="INV-001",
            status=ApprovalStatus.APPROVED,
            reasoning="All checks passed",
        )
        assert decision.effective_escalation_reason is None
