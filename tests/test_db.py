"""Tests for database queries."""

from pathlib import Path

import pytest

from src.db.queries import (
    check_duplicate_invoice,
    check_stock,
    check_vendor,
    close_connection,
    lookup_item,
    record_processed_invoice,
)
from src.db.seed import seed_database


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Create a fresh test database."""
    db_path = tmp_path / "test.db"
    seed_database(db_path, reset=True)
    close_connection()  # Reset connection cache
    return db_path


class TestLookupItem:
    """Tests for item lookup functionality."""

    def test_exact_match(self, test_db: Path) -> None:
        result = lookup_item("WidgetA", test_db)
        assert result.found
        assert result.item_name == "WidgetA"
        assert result.stock == 15
        assert result.unit_price == 250.0
        assert not result.fuzzy_matched

    def test_case_insensitive_match(self, test_db: Path) -> None:
        result = lookup_item("widgeta", test_db)
        assert result.found
        assert result.item_name == "WidgetA"
        assert not result.fuzzy_matched

    def test_space_removal_match(self, test_db: Path) -> None:
        """'Widget A' should match 'WidgetA' via space removal."""
        result = lookup_item("Widget A", test_db)
        assert result.found
        assert result.item_name == "WidgetA"
        assert result.fuzzy_matched  # Marked as fuzzy due to transformation
        assert result.match_score == 95.0

    def test_unknown_item_not_found(self, test_db: Path) -> None:
        """WidgetC should NOT match - it's a genuinely unknown item."""
        result = lookup_item("WidgetC", test_db)
        assert not result.found
        assert result.item_name == "WidgetC"

    def test_completely_different_item(self, test_db: Path) -> None:
        result = lookup_item("SuperGizmo", test_db)
        assert not result.found

    def test_fuzzy_match_typo(self, test_db: Path) -> None:
        """'Widgeta' with wrong case should still match exactly (case insensitive)."""
        result = lookup_item("WIDGETA", test_db)
        assert result.found
        assert result.item_name == "WidgetA"

    def test_gadget_with_space(self, test_db: Path) -> None:
        """'Gadget X' should match 'GadgetX'."""
        result = lookup_item("Gadget X", test_db)
        assert result.found
        assert result.item_name == "GadgetX"
        assert result.fuzzy_matched


class TestCheckStock:
    """Tests for stock checking functionality."""

    def test_stock_available(self, test_db: Path) -> None:
        result = check_stock("WidgetA", 10, test_db)
        assert result["available"]
        assert result["stock"] == 15
        assert result["requested"] == 10
        assert result["remaining_after"] == 5

    def test_stock_exact_match(self, test_db: Path) -> None:
        """Request exactly what's in stock."""
        result = check_stock("WidgetB", 10, test_db)
        assert result["available"]
        assert result["remaining_after"] == 0

    def test_stock_exceeded(self, test_db: Path) -> None:
        result = check_stock("GadgetX", 20, test_db)
        assert not result["available"]
        assert result["error"] == "STOCK_EXCEEDED"
        assert result["stock"] == 5
        assert result["shortage"] == 15

    def test_zero_stock(self, test_db: Path) -> None:
        result = check_stock("FakeItem", 1, test_db)
        assert not result["available"]
        assert result["error"] == "ZERO_STOCK"

    def test_unknown_item(self, test_db: Path) -> None:
        result = check_stock("MegaSprocket", 5, test_db)
        assert not result["available"]
        assert result["error"] == "UNKNOWN_ITEM"


class TestCheckVendor:
    """Tests for vendor checking functionality."""

    def test_known_vendor(self, test_db: Path) -> None:
        result = check_vendor("Widgets Inc.", test_db)
        assert result.found
        assert result.name == "Widgets Inc."
        assert result.status == "active"
        assert not result.is_blacklisted

    def test_blacklisted_vendor(self, test_db: Path) -> None:
        result = check_vendor("Fraudster LLC", test_db)
        assert result.found
        assert result.is_blacklisted
        assert result.status == "blacklisted"

    def test_unknown_vendor(self, test_db: Path) -> None:
        result = check_vendor("New Vendor Corp", test_db)
        assert not result.found

    def test_case_insensitive_vendor(self, test_db: Path) -> None:
        result = check_vendor("widgets inc.", test_db)
        assert result.found
        assert result.name == "Widgets Inc."


class TestDuplicateInvoice:
    """Tests for duplicate invoice detection."""

    def test_record_and_check_duplicate(self, test_db: Path) -> None:
        # First recording should succeed
        success = record_processed_invoice(
            invoice_number="INV-TEST-001",
            status="paid",
            total_amount=1000.0,
            vendor="Test Corp",
            run_id="run-test-001",
            db_path=test_db,
        )
        assert success

        # Check should find the duplicate
        dup = check_duplicate_invoice("INV-TEST-001", test_db)
        assert dup is not None
        assert dup["invoice_number"] == "INV-TEST-001"
        assert dup["status"] == "paid"

    def test_no_duplicate(self, test_db: Path) -> None:
        dup = check_duplicate_invoice("INV-NEVER-PROCESSED", test_db)
        assert dup is None

    def test_duplicate_recording_fails(self, test_db: Path) -> None:
        # First recording
        record_processed_invoice(
            invoice_number="INV-DUP-001",
            status="paid",
            total_amount=500.0,
            vendor="Test",
            run_id="run-1",
            db_path=test_db,
        )

        # Second recording with same invoice number should fail
        success = record_processed_invoice(
            invoice_number="INV-DUP-001",
            status="paid",
            total_amount=600.0,
            vendor="Test",
            run_id="run-2",
            db_path=test_db,
        )
        assert not success
