"""Tests for feedback loop / corrections."""

from pathlib import Path

import pytest

from src.feedback.corrections import (
    Correction,
    CorrectionStore,
    CorrectionType,
)


@pytest.fixture
def store(tmp_path: Path) -> CorrectionStore:
    """Create a test correction store."""
    return CorrectionStore(tmp_path / "test_corrections.db")


class TestCorrectionStore:
    """Tests for CorrectionStore."""

    def test_record_correction(self, store: CorrectionStore) -> None:
        """Should record a correction."""
        correction = Correction(
            run_id="run-123",
            invoice_number="INV-1001",
            correction_type=CorrectionType.VENDOR_NAME,
            original_value="Acme Inc",
            corrected_value="Acme Corporation",
            corrected_by="reviewer@example.com",
        )
        
        recorded = store.record(correction)
        
        assert recorded.id is not None
        assert recorded.correction_type == CorrectionType.VENDOR_NAME

    def test_item_mapping_creates_learned_mapping(self, store: CorrectionStore) -> None:
        """Should create learned mapping for item corrections."""
        store.record(Correction(
            run_id="run-1",
            invoice_number="INV-1001",
            correction_type=CorrectionType.ITEM_MAPPING,
            original_value="Widget A",
            corrected_value="WidgetA",
            corrected_by="reviewer@example.com",
        ))
        
        mapping = store.get_mapping(CorrectionType.ITEM_MAPPING, "Widget A")
        
        assert mapping == "WidgetA"

    def test_get_all_mappings(self, store: CorrectionStore) -> None:
        """Should get all mappings of a type."""
        store.record(Correction(
            run_id="run-1",
            invoice_number="INV-1001",
            correction_type=CorrectionType.ITEM_MAPPING,
            original_value="Widget A",
            corrected_value="WidgetA",
            corrected_by="user",
        ))
        store.record(Correction(
            run_id="run-2",
            invoice_number="INV-1002",
            correction_type=CorrectionType.ITEM_MAPPING,
            original_value="Gadget X",
            corrected_value="GadgetX",
            corrected_by="user",
        ))
        
        mappings = store.get_all_mappings(CorrectionType.ITEM_MAPPING)
        
        assert len(mappings) == 2
        assert mappings["Widget A"] == "WidgetA"
        assert mappings["Gadget X"] == "GadgetX"

    def test_mapping_occurrence_count_increases(self, store: CorrectionStore) -> None:
        """Should increment count when same mapping is recorded."""
        for _ in range(3):
            store.record(Correction(
                run_id=f"run-{_}",
                invoice_number=f"INV-{_}",
                correction_type=CorrectionType.ITEM_MAPPING,
                original_value="Widget A",
                corrected_value="WidgetA",
                corrected_by="user",
            ))
        
        common = store.get_common_corrections(
            CorrectionType.ITEM_MAPPING,
            min_occurrences=1,
        )
        
        assert len(common) == 1
        assert common[0]["count"] == 3

    def test_get_common_corrections(self, store: CorrectionStore) -> None:
        """Should find commonly made corrections."""
        # Same correction made multiple times
        for i in range(5):
            store.record(Correction(
                run_id=f"run-{i}",
                invoice_number=f"INV-{i}",
                correction_type=CorrectionType.VENDOR_NAME,
                original_value="Acme Inc",
                corrected_value="Acme Corporation",
                corrected_by="user",
            ))
        
        # One-off correction
        store.record(Correction(
            run_id="run-x",
            invoice_number="INV-X",
            correction_type=CorrectionType.VENDOR_NAME,
            original_value="XYZ Ltd",
            corrected_value="XYZ Limited",
            corrected_by="user",
        ))
        
        common = store.get_common_corrections(
            CorrectionType.VENDOR_NAME,
            min_occurrences=3,
        )
        
        assert len(common) == 1
        assert common[0]["original"] == "Acme Inc"
        assert common[0]["count"] == 5

    def test_get_for_invoice(self, store: CorrectionStore) -> None:
        """Should get all corrections for an invoice."""
        store.record(Correction(
            run_id="run-1",
            invoice_number="INV-1001",
            correction_type=CorrectionType.VENDOR_NAME,
            original_value="A",
            corrected_value="B",
            corrected_by="user",
        ))
        store.record(Correction(
            run_id="run-1",
            invoice_number="INV-1001",
            correction_type=CorrectionType.AMOUNT,
            original_value=100,
            corrected_value=150,
            corrected_by="user",
        ))
        store.record(Correction(
            run_id="run-2",
            invoice_number="INV-1002",
            correction_type=CorrectionType.VENDOR_NAME,
            original_value="C",
            corrected_value="D",
            corrected_by="user",
        ))
        
        corrections = store.get_for_invoice("INV-1001")
        
        assert len(corrections) == 2

    def test_correction_rate_stats(self, store: CorrectionStore) -> None:
        """Should calculate correction statistics."""
        store.record(Correction(
            run_id="run-1",
            invoice_number="INV-1001",
            correction_type=CorrectionType.VENDOR_NAME,
            original_value="A",
            corrected_value="B",
            corrected_by="user",
            source_format="pdf",
            confidence_was=0.8,
        ))
        store.record(Correction(
            run_id="run-2",
            invoice_number="INV-1002",
            correction_type=CorrectionType.AMOUNT,
            original_value=100,
            corrected_value=150,
            corrected_by="user",
            source_format="txt",
            confidence_was=0.6,
        ))
        
        stats = store.get_correction_rate()
        
        assert stats["total_corrections"] == 2
        assert "vendor_name" in stats["by_type"]
        assert "amount" in stats["by_type"]
        assert stats["avg_confidence_when_wrong"] == 0.7

    def test_no_mapping_returns_none(self, store: CorrectionStore) -> None:
        """Should return None for unknown mappings."""
        mapping = store.get_mapping(CorrectionType.ITEM_MAPPING, "Unknown Item")
        
        assert mapping is None
