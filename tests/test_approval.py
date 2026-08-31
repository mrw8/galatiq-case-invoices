"""Tests for approval agent."""

from decimal import Decimal

import pytest

from src.agents.approval import ApprovalAgent
from src.llm.client import MockClient
from src.models.invoice import (
    ApprovalStatus,
    FlagDetail,
    Invoice,
    LineItem,
    ValidationFlag,
    ValidationResult,
    Vendor,
)
from src.models.pipeline import PipelineState


@pytest.fixture
def mock_client() -> MockClient:
    """Provide a mock LLM client."""
    return MockClient()


@pytest.fixture
def approval_agent(mock_client: MockClient) -> ApprovalAgent:
    """Create approval agent with mock client."""
    return ApprovalAgent(mock_client)


def make_invoice(
    invoice_number: str = "INV-TEST",
    vendor_name: str = "Acme Corp",
    total: float = 1000.0,
) -> Invoice:
    """Helper to create test invoices."""
    return Invoice(
        invoice_number=invoice_number,
        vendor=Vendor(name=vendor_name),
        line_items=[LineItem(item="WidgetA", quantity=5, unit_price=Decimal("200"))],
        total=Decimal(str(total)),
    )


def make_validation_result(
    invoice_number: str = "INV-TEST",
    flags: list[ValidationFlag] | None = None,
) -> ValidationResult:
    """Helper to create validation results."""
    flag_details = []
    if flags:
        for flag in flags:
            flag_details.append(FlagDetail(flag=flag, message=f"Test: {flag.value}"))

    return ValidationResult(
        invoice_number=invoice_number,
        is_valid=len(flag_details) == 0,
        flags=flag_details,
    )


def make_state(
    invoice: Invoice,
    validation_result: ValidationResult,
) -> PipelineState:
    """Helper to create pipeline state."""
    return PipelineState(
        run_id="test-run",
        source_path="test.txt",
        invoice=invoice,
        validation_result=validation_result,
    )


class TestApprovalAgent:
    """Tests for ApprovalAgent."""

    def test_clean_invoice_approved(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with no flags should be approved."""
        invoice = make_invoice()
        validation = make_validation_result()
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.APPROVED
        assert result.error is None

    def test_unknown_item_rejected(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with UNKNOWN_ITEM should be rejected."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.UNKNOWN_ITEM])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.REJECTED
        assert "hard_reject_flags" in result.approval_decision.rules_applied

    def test_stock_exceeded_rejected(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with STOCK_EXCEEDED should be rejected."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.STOCK_EXCEEDED])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_zero_stock_rejected(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with ZERO_STOCK should be rejected."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.ZERO_STOCK])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_negative_qty_rejected(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with NEGATIVE_QTY should be rejected."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.NEGATIVE_QTY])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_fraud_suspect_rejected(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with FRAUD_SUSPECT should be rejected."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.FRAUD_SUSPECT])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_blacklisted_vendor_rejected(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with BLACKLISTED_VENDOR should be rejected."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.BLACKLISTED_VENDOR])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_missing_vendor_rejected(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with MISSING_VENDOR should be rejected."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.MISSING_VENDOR])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_foreign_currency_needs_human(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with FOREIGN_CURRENCY should need human review."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.FOREIGN_CURRENCY])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.NEEDS_HUMAN
        assert "human_review_flags" in result.approval_decision.rules_applied

    def test_duplicate_invoice_needs_human(self, approval_agent: ApprovalAgent) -> None:
        """Invoice with DUPLICATE_INVOICE should need human review."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.DUPLICATE_INVOICE])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.NEEDS_HUMAN

    def test_high_value_triggers_scrutiny(self, approval_agent: ApprovalAgent) -> None:
        """High value invoice should trigger extra scrutiny rule."""
        invoice = make_invoice(total=15000.0)
        validation = make_validation_result(flags=[ValidationFlag.HIGH_VALUE])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        # HIGH_VALUE alone doesn't reject - it adds scrutiny
        assert "high_value_scrutiny" in result.approval_decision.rules_applied

    def test_fuzzy_match_triggers_conditional(self, approval_agent: ApprovalAgent) -> None:
        """Fuzzy match should trigger conditional rule."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[ValidationFlag.FUZZY_MATCH])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert "fuzzy_match_conditional" in result.approval_decision.rules_applied

    def test_multiple_hard_flags_rejected(self, approval_agent: ApprovalAgent) -> None:
        """Multiple hard flags should still result in rejection."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[
            ValidationFlag.UNKNOWN_ITEM,
            ValidationFlag.STOCK_EXCEEDED,
            ValidationFlag.FRAUD_SUSPECT,
        ])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_hard_flag_trumps_human_review_flag(self, approval_agent: ApprovalAgent) -> None:
        """Hard flag should take precedence over human review flag."""
        invoice = make_invoice()
        validation = make_validation_result(flags=[
            ValidationFlag.UNKNOWN_ITEM,  # Hard reject
            ValidationFlag.FOREIGN_CURRENCY,  # Human review
        ])
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        # Hard reject takes precedence
        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_reasoning_provided(self, approval_agent: ApprovalAgent) -> None:
        """All decisions should include reasoning."""
        invoice = make_invoice()
        validation = make_validation_result()
        state = make_state(invoice, validation)

        result = approval_agent.run(state)

        assert result.approval_decision is not None
        assert len(result.approval_decision.reasoning) > 0

    def test_missing_invoice_sets_error(self, approval_agent: ApprovalAgent) -> None:
        """Missing invoice should set error."""
        state = PipelineState(
            run_id="test-run",
            source_path="test.txt",
            invoice=None,
            validation_result=make_validation_result(),
        )

        result = approval_agent.run(state)

        assert result.error is not None
        assert result.error_stage == "approval"

    def test_missing_validation_sets_error(self, approval_agent: ApprovalAgent) -> None:
        """Missing validation result should set error."""
        state = PipelineState(
            run_id="test-run",
            source_path="test.txt",
            invoice=make_invoice(),
            validation_result=None,
        )

        result = approval_agent.run(state)

        assert result.error is not None
        assert result.error_stage == "approval"
