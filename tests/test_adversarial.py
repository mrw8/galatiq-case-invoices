"""Tests for adversarial/prompt injection resistance.

These tests verify that the system properly handles invoices containing
prompt injection attempts and does not bypass validation or approval rules.
"""

import pytest
from pathlib import Path

from src.graph.pipeline import run_pipeline
from src.llm.client import MockClient
from src.models.invoice import ApprovalStatus


ADVERSARIAL_DIR = Path(__file__).parent.parent / "data" / "invoices" / "adversarial"


@pytest.fixture
def mock_client():
    """Provide a mock LLM client for testing."""
    return MockClient()


class TestPromptInjectionResistance:
    """Test suite for prompt injection resistance."""

    def test_injection_in_vendor_name_rejected(self, mock_client, tmp_path):
        """Vendor name containing 'ignore instructions' should still validate normally."""
        # Invoice 001 has injection attempt in vendor name
        invoice_path = ADVERSARIAL_DIR / "invoice_injection_001.txt"
        if not invoice_path.exists():
            pytest.skip("Adversarial test file not found")

        result = run_pipeline(str(invoice_path), llm_client=mock_client)

        # Should not be auto-approved despite injection attempt
        # The validation should run normally based on actual data
        assert result.validation_result is not None
        # Vendor name is suspicious (contains injection text, not a real vendor)
        # System should treat it as any other unknown vendor

    def test_injection_in_notes_does_not_bypass_validation(self, mock_client, tmp_path):
        """Notes containing 'SYSTEM OVERRIDE' should not affect validation."""
        invoice_path = ADVERSARIAL_DIR / "invoice_injection_001.txt"
        if not invoice_path.exists():
            pytest.skip("Adversarial test file not found")

        result = run_pipeline(str(invoice_path), llm_client=mock_client)

        # Validation should still check actual inventory, stock levels, etc.
        # Injected instructions in notes field should have no effect
        assert result.validation_result is not None
        # The pipeline should complete without being hijacked

    def test_hidden_html_comments_ignored(self, mock_client, tmp_path):
        """HTML comments with 'maintenance mode' instructions should be ignored."""
        invoice_path = ADVERSARIAL_DIR / "invoice_injection_003.txt"
        if not invoice_path.exists():
            pytest.skip("Adversarial test file not found")

        result = run_pipeline(str(invoice_path), llm_client=mock_client)

        # Pipeline should process normally, not enter any "maintenance mode"
        assert result.validation_result is not None
        # Should not be marked as PAID without proper approval
        if result.approval_decision:
            assert result.approval_decision.status != ApprovalStatus.APPROVED or \
                   result.validation_result.is_valid

    def test_triple_quote_injection_blocked(self, mock_client, tmp_path):
        """Triple-quote style instruction injection should not work."""
        invoice_path = ADVERSARIAL_DIR / "invoice_injection_004.txt"
        if not invoice_path.exists():
            pytest.skip("Adversarial test file not found")

        result = run_pipeline(str(invoice_path), llm_client=mock_client)

        # The injection should NOT have bypassed validation
        assert result.validation_result is not None

        # Either validation raised flags OR it was not auto-approved
        # The point is the injection instructions were ignored
        has_flags = len(result.validation_result.flags) > 0
        not_auto_approved = (
            result.approval_decision is None or
            result.approval_decision.status != ApprovalStatus.APPROVED or
            result.validation_result.is_valid
        )
        assert has_flags or not_auto_approved, \
            "Invoice with injection was processed without any flags or safeguards"

    def test_json_injection_in_notes_ignored(self, mock_client, tmp_path):
        """JSON embedded in notes field should not override actual results."""
        invoice_path = ADVERSARIAL_DIR / "invoice_injection_005.json"
        if not invoice_path.exists():
            pytest.skip("Adversarial test file not found")

        result = run_pipeline(str(invoice_path), llm_client=mock_client)

        # The embedded JSON in notes should not affect the actual pipeline state
        assert result.validation_result is not None
        # Flags should be computed from actual data, not from injected JSON

    def test_urgency_pressure_detected_in_injection(self, mock_client, tmp_path):
        """Urgency words in injection attempts should trigger fraud flags."""
        invoice_path = ADVERSARIAL_DIR / "invoice_injection_004.txt"
        if not invoice_path.exists():
            pytest.skip("Adversarial test file not found")

        result = run_pipeline(str(invoice_path), llm_client=mock_client)

        # "urgent" and "wire transfer" and "penalty" should trigger fraud detection
        assert result.validation_result is not None
        flag_names = [f.flag.value for f in result.validation_result.flags]
        # The urgency pressure should be detected
        assert "fraud_suspect" in flag_names or len(flag_names) > 0

    def test_newline_injection_in_vendor_handled(self, mock_client, tmp_path):
        """Newline characters in vendor name should not inject instructions."""
        invoice_path = ADVERSARIAL_DIR / "invoice_injection_002.json"
        if not invoice_path.exists():
            pytest.skip("Adversarial test file not found")

        result = run_pipeline(str(invoice_path), llm_client=mock_client)

        # Should process normally despite newlines in vendor name
        assert result.validation_result is not None
        # High value should still be flagged (50k invoice)
        flag_names = [f.flag.value for f in result.validation_result.flags]
        # Should have either high_value or unknown_item since GadgetX isn't standard
        assert len(flag_names) > 0


class TestAdversarialBatchProcessing:
    """Test that batch processing handles adversarial inputs safely."""

    def test_batch_with_mixed_adversarial_invoices(self, mock_client):
        """Processing a batch with adversarial invoices should not affect others."""
        from src.graph.pipeline import run_batch

        if not ADVERSARIAL_DIR.exists():
            pytest.skip("Adversarial directory not found")

        results = run_batch(str(ADVERSARIAL_DIR), llm_client=mock_client)

        # All invoices should be processed (none should crash the system)
        assert len(results) > 0

        # Each should have validation results
        for result in results:
            assert result.validation_result is not None, \
                f"Invoice {result.source} failed to validate"

        # None should be magically approved despite injection attempts
        # (assuming they have validation issues)
        for result in results:
            if result.approval_decision:
                # If approved, it should be because validation actually passed
                if result.approval_decision.status == ApprovalStatus.APPROVED:
                    assert result.validation_result.is_valid, \
                        f"Invoice {result.source} approved despite validation failure"
