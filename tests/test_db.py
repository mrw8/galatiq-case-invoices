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
        assert result.stock > 0  # Has some stock
        assert result.unit_price > 0  # Has a price
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

    def test_stock_available_when_sufficient(self, test_db: Path) -> None:
        """Requesting less than available stock should succeed."""
        # First find out how much stock exists
        item = lookup_item("WidgetA", test_db)
        request_qty = item.stock // 2  # Request half of available
        
        result = check_stock("WidgetA", request_qty, test_db)
        
        assert result["available"]
        assert result["stock"] == item.stock
        assert result["requested"] == request_qty
        assert result["remaining_after"] == item.stock - request_qty

    def test_stock_exact_match(self, test_db: Path) -> None:
        """Request exactly what's in stock should succeed."""
        item = lookup_item("WidgetB", test_db)
        
        result = check_stock("WidgetB", item.stock, test_db)
        
        assert result["available"]
        assert result["remaining_after"] == 0

    def test_stock_exceeded(self, test_db: Path) -> None:
        """Requesting more than available should fail with STOCK_EXCEEDED."""
        item = lookup_item("GadgetX", test_db)
        request_qty = item.stock + 50  # Request more than available
        
        result = check_stock("GadgetX", request_qty, test_db)
        
        assert not result["available"]
        assert result["error"] == "STOCK_EXCEEDED"
        assert result["stock"] == item.stock
        assert result["shortage"] == request_qty - item.stock

    def test_zero_stock(self, test_db: Path) -> None:
        """Item with zero stock should fail with ZERO_STOCK."""
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

    def test_duplicate_run_id_fails(self, test_db: Path) -> None:
        # First recording
        record_processed_invoice(
            invoice_number="INV-DUP-001",
            status="paid",
            total_amount=500.0,
            vendor="Test",
            run_id="run-1",
            db_path=test_db,
        )

        # Second recording with same run_id should fail (prevents double-processing)
        success = record_processed_invoice(
            invoice_number="INV-DUP-001",
            status="paid",
            total_amount=600.0,
            vendor="Test",
            run_id="run-1",  # Same run_id
            db_path=test_db,
        )
        assert not success

    def test_same_invoice_different_runs_allowed(self, test_db: Path) -> None:
        # First recording
        record_processed_invoice(
            invoice_number="INV-DUP-002",
            status="paid",
            total_amount=500.0,
            vendor="Test",
            run_id="run-a",
            db_path=test_db,
        )

        # Second recording with same invoice but different run_id succeeds
        # (e.g., duplicate approved after human review)
        success = record_processed_invoice(
            invoice_number="INV-DUP-002",
            status="paid_after_review",
            total_amount=500.0,
            vendor="Test",
            run_id="run-b",  # Different run_id
            db_path=test_db,
        )
        assert success


class TestSQLInjectionProtection:
    """Tests for SQL injection protection."""

    def test_deduct_inventory_rejects_sql_injection_in_item_name(self, test_db: Path) -> None:
        """SQL injection attempts in item names should be rejected."""
        from src.db.queries import deduct_inventory
        
        # Various SQL injection attempts
        injection_attempts = [
            "'; DROP TABLE inventory; --",
            "WidgetA'; DELETE FROM inventory WHERE '1'='1",
            "WidgetA OR 1=1",
            "WidgetA; UPDATE inventory SET stock=999 WHERE item='WidgetA",
            "WidgetA\"; DROP TABLE inventory; --",
        ]
        
        for injection in injection_attempts:
            result = deduct_inventory([(injection, 1)], test_db)
            # Should fail with invalid characters, not execute SQL
            assert len(result["deducted"]) == 0
            assert len(result["errors"]) == 1
            assert result["errors"][0]["error"] in ("invalid_characters_in_name", "not_found")
        
        # Verify inventory table still exists and is intact
        import sqlite3
        conn = sqlite3.connect(test_db)
        cursor = conn.execute("SELECT COUNT(*) FROM inventory")
        count = cursor.fetchone()[0]
        conn.close()
        assert count > 0  # Table exists and has data

    def test_deduct_inventory_rejects_invalid_quantity(self, test_db: Path) -> None:
        """Invalid quantities should be rejected."""
        from src.db.queries import deduct_inventory
        
        invalid_quantities = [
            ("WidgetA", "DROP TABLE", "invalid_quantity_type"),
            ("WidgetA", -5, "quantity_must_be_positive"),
            ("WidgetA", 0, "quantity_must_be_positive"),
            ("WidgetA", None, "invalid_quantity_type"),
            ("WidgetA", "10; DROP TABLE inventory", "invalid_quantity_type"),
        ]
        
        for item, qty, expected_error in invalid_quantities:
            result = deduct_inventory([(item, qty)], test_db)
            assert len(result["deducted"]) == 0, f"Should reject {qty}"
            assert len(result["errors"]) == 1
            assert result["errors"][0]["error"] == expected_error, f"Expected {expected_error} for {qty}"

    def test_deduct_inventory_rejects_decimal_quantities(self, test_db: Path) -> None:
        """Decimal quantities should be rejected - inventory must be whole numbers."""
        from src.db.queries import deduct_inventory
        
        decimal_quantities = [
            ("WidgetA", 2.5),
            ("WidgetA", 0.1),
            ("WidgetA", 1.99),
            ("WidgetA", 10.001),
        ]
        
        for item, qty in decimal_quantities:
            result = deduct_inventory([(item, qty)], test_db)
            assert len(result["deducted"]) == 0, f"Should reject decimal {qty}"
            assert len(result["errors"]) == 1
            assert result["errors"][0]["error"] == "decimal_quantity_not_allowed"

    def test_deduct_inventory_accepts_whole_number_floats(self, test_db: Path) -> None:
        """Whole number floats like 5.0 should be accepted and converted to int."""
        from src.db.queries import deduct_inventory
        
        # Get initial stock
        import sqlite3
        conn = sqlite3.connect(test_db)
        cursor = conn.execute("SELECT stock FROM inventory WHERE item = 'WidgetA'")
        initial_stock = cursor.fetchone()[0]
        conn.close()
        
        # Deduct with a whole-number float
        result = deduct_inventory([("WidgetA", 2.0)], test_db)
        assert result["success"] is True
        assert len(result["deducted"]) == 1
        assert result["deducted"][0]["quantity"] == 2  # Should be int, not float
        
        # Verify stock was deducted
        conn = sqlite3.connect(test_db)
        cursor = conn.execute("SELECT stock FROM inventory WHERE item = 'WidgetA'")
        new_stock = cursor.fetchone()[0]
        conn.close()
        assert new_stock == initial_stock - 2

    def test_lookup_item_safe_from_injection(self, test_db: Path) -> None:
        """Lookup should not execute injected SQL."""
        result = lookup_item("'; DROP TABLE inventory; --", test_db)
        assert result.found is False
        
        # Verify table still exists
        import sqlite3
        conn = sqlite3.connect(test_db)
        cursor = conn.execute("SELECT COUNT(*) FROM inventory")
        count = cursor.fetchone()[0]
        conn.close()
        assert count > 0
