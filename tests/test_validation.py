"""Tests for validation agent."""

from decimal import Decimal
from pathlib import Path

import pytest

from src.agents.validation import ValidationAgent
from src.db.queries import close_connection
from src.db.seed import seed_database
from src.models.invoice import Invoice, LineItem, ValidationFlag, Vendor
from src.models.pipeline import PipelineState


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Create a fresh test database."""
    db_path = tmp_path / "test.db"
    seed_database(db_path, reset=True)
    close_connection()
    return db_path


@pytest.fixture
def validation_agent(test_db: Path) -> ValidationAgent:
    """Create validation agent with test database."""
    return ValidationAgent(db_path=str(test_db))


def make_state(invoice: Invoice) -> PipelineState:
    """Helper to create pipeline state with invoice."""
    return PipelineState(
        run_id="test-run",
        source_path="test.txt",
        invoice=invoice,
    )


def make_invoice(
    invoice_number: str = "INV-TEST",
    vendor_name: str = "Acme Corp",
    items: list[tuple[str, int, float]] | None = None,
    total: float | None = None,
    currency: str = "USD",
    due_date: str = "2026-02-15",
) -> Invoice:
    """Helper to create test invoices."""
    import datetime

    if items is None:
        items = [("WidgetA", 5, 250.0)]

    line_items = [
        LineItem(item=name, quantity=qty, unit_price=Decimal(str(price)))
        for name, qty, price in items
    ]

    if total is None:
        total = sum(qty * price for _, qty, price in items)

    return Invoice(
        invoice_number=invoice_number,
        vendor=Vendor(name=vendor_name),
        line_items=line_items,
        total=Decimal(str(total)),
        currency=currency,
        due_date=datetime.date.fromisoformat(due_date),
    )


class TestValidationAgent:
    """Tests for ValidationAgent."""

    def test_valid_invoice_passes(self, validation_agent: ValidationAgent) -> None:
        """Clean invoice with valid items and stock should pass."""
        invoice = make_invoice(
            items=[("WidgetA", 5, 250.0), ("WidgetB", 3, 500.0)],
        )
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        assert result.validation_result.is_valid
        assert len(result.validation_result.flags) == 0

    def test_unknown_item_flagged(self, validation_agent: ValidationAgent) -> None:
        """Unknown item should be flagged."""
        invoice = make_invoice(items=[("SuperGizmo", 5, 100.0)])
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.UNKNOWN_ITEM in flags

    def test_stock_exceeded_flagged(self, validation_agent: ValidationAgent) -> None:
        """Requesting more than available stock should be flagged."""
        invoice = make_invoice(items=[("GadgetX", 20, 750.0)])  # Only 5 in stock
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.STOCK_EXCEEDED in flags

    def test_zero_stock_flagged(self, validation_agent: ValidationAgent) -> None:
        """Item with zero stock should be flagged."""
        invoice = make_invoice(items=[("FakeItem", 1, 1000.0)])
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.ZERO_STOCK in flags

    def test_negative_quantity_flagged(self, validation_agent: ValidationAgent) -> None:
        """Negative quantity should be flagged."""
        invoice = Invoice(
            invoice_number="INV-NEG",
            vendor=Vendor(name="Test"),
            line_items=[
                LineItem(item="WidgetA", quantity=-5, unit_price=Decimal("250")),
            ],
            total=Decimal("-1250"),
        )
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.NEGATIVE_QTY in flags

    def test_missing_vendor_flagged(self, validation_agent: ValidationAgent) -> None:
        """Empty vendor should be flagged."""
        invoice = make_invoice(vendor_name="")
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.MISSING_VENDOR in flags

    def test_blacklisted_vendor_flagged(self, validation_agent: ValidationAgent) -> None:
        """Blacklisted vendor should be flagged."""
        invoice = make_invoice(vendor_name="Fraudster LLC")
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.BLACKLISTED_VENDOR in flags

    def test_high_value_flagged(self, validation_agent: ValidationAgent) -> None:
        """Invoice >= $10K should be flagged."""
        invoice = make_invoice(
            items=[("WidgetA", 10, 250.0), ("WidgetB", 10, 500.0)],  # $7500 total
            total=15000.0,  # Override with high value
        )
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.HIGH_VALUE in flags

    def test_foreign_currency_flagged(self, validation_agent: ValidationAgent) -> None:
        """Non-USD currency should be flagged."""
        invoice = make_invoice(currency="EUR")
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.FOREIGN_CURRENCY in flags

    def test_fuzzy_match_flagged(self, validation_agent: ValidationAgent) -> None:
        """Fuzzy matched item (space in name) should be flagged."""
        invoice = make_invoice(items=[("Widget A", 5, 250.0)])  # Space in name
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.FUZZY_MATCH in flags

    def test_aggregates_duplicate_items(self, validation_agent: ValidationAgent) -> None:
        """Should aggregate quantities for duplicate items before checking stock."""
        # WidgetA stock is 15, two line items totaling 16 should fail
        invoice = Invoice(
            invoice_number="INV-DUP",
            vendor=Vendor(name="Test Vendor"),
            line_items=[
                LineItem(item="WidgetA", quantity=10, unit_price=Decimal("250")),
                LineItem(item="WidgetA", quantity=6, unit_price=Decimal("250")),  # Total 16 > 15
            ],
            total=Decimal("4000"),
        )
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.STOCK_EXCEEDED in flags

    def test_fraud_indicators_detected(self, validation_agent: ValidationAgent) -> None:
        """Should detect fraud indicators in invoice."""
        import datetime

        invoice = Invoice(
            invoice_number="INV-FRAUD",
            vendor=Vendor(name="Scam Corp"),  # Contains suspicious word
            line_items=[
                LineItem(item="WidgetA", quantity=5, unit_price=Decimal("250")),
            ],
            total=Decimal("1250"),
            notes="URGENT! Pay immediately via wire transfer!",
            due_date=datetime.date(2026, 2, 15),
        )
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.FRAUD_SUSPECT in flags

    def test_multiple_flags(self, validation_agent: ValidationAgent) -> None:
        """Invoice can have multiple flags."""
        invoice = Invoice(
            invoice_number="INV-MULTI",
            vendor=Vendor(name=""),  # Missing vendor
            line_items=[
                LineItem(item="UnknownItem", quantity=100, unit_price=Decimal("1000")),
            ],
            total=Decimal("100000"),  # High value
            currency="EUR",  # Foreign currency
        )
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.MISSING_VENDOR in flags
        assert ValidationFlag.UNKNOWN_ITEM in flags
        assert ValidationFlag.HIGH_VALUE in flags
        assert ValidationFlag.FOREIGN_CURRENCY in flags

    def test_has_hard_flags_property(self, validation_agent: ValidationAgent) -> None:
        """ValidationResult.has_hard_flags should work correctly."""
        invoice = make_invoice(items=[("SuperGizmo", 5, 100.0)])  # Unknown item
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        assert result.validation_result.has_hard_flags

    def test_needs_human_review_property(self, validation_agent: ValidationAgent) -> None:
        """ValidationResult.needs_human_review should work correctly."""
        invoice = make_invoice(currency="EUR")
        state = make_state(invoice)

        result = validation_agent.run(state)

        assert result.validation_result is not None
        assert result.validation_result.needs_human_review
