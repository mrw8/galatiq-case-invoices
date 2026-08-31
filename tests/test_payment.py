"""Tests for payment agent."""

from decimal import Decimal
from pathlib import Path

import pytest

from src.agents.payment import PaymentAgent, mock_payment
from src.db.queries import check_duplicate_invoice, close_connection
from src.db.seed import seed_database
from src.models.invoice import (
    ApprovalDecision,
    ApprovalStatus,
    Invoice,
    LineItem,
    Vendor,
)
from src.models.pipeline import PipelineState


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Create a fresh test database."""
    db_path = tmp_path / "test.db"
    seed_database(db_path, reset=True)
    close_connection()
    return db_path


@pytest.fixture
def rejections_file(tmp_path: Path) -> Path:
    """Temporary rejections file."""
    return tmp_path / "rejections.jsonl"


@pytest.fixture
def payment_agent(test_db: Path, rejections_file: Path) -> PaymentAgent:
    """Create payment agent with test paths."""
    return PaymentAgent(
        rejections_path=str(rejections_file),
        db_path=str(test_db),
    )


def make_invoice(invoice_number: str = "INV-TEST") -> Invoice:
    """Helper to create test invoices."""
    return Invoice(
        invoice_number=invoice_number,
        vendor=Vendor(name="Acme Corp"),
        line_items=[LineItem(item="WidgetA", quantity=5, unit_price=Decimal("200"))],
        total=Decimal("1000"),
    )


def make_approval(
    invoice_number: str = "INV-TEST",
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    reasoning: str = "Test approval",
) -> ApprovalDecision:
    """Helper to create approval decisions."""
    return ApprovalDecision(
        invoice_number=invoice_number,
        status=status,
        reasoning=reasoning,
    )


def make_state(
    invoice: Invoice,
    approval: ApprovalDecision,
) -> PipelineState:
    """Helper to create pipeline state."""
    return PipelineState(
        run_id="test-run",
        source_path="test.txt",
        invoice=invoice,
        approval_decision=approval,
    )


class TestPaymentAgent:
    """Tests for PaymentAgent."""

    def test_approved_invoice_paid(
        self, payment_agent: PaymentAgent, capsys: pytest.CaptureFixture
    ) -> None:
        """Approved invoice should be paid."""
        invoice = make_invoice("INV-PAY-001")
        approval = make_approval("INV-PAY-001", ApprovalStatus.APPROVED)
        state = make_state(invoice, approval)

        result = payment_agent.run(state)

        assert result.payment_status == "success"
        assert result.payment_reference is not None
        assert result.payment_reference.startswith("PAY-")
        assert result.completed_at is not None

        # Check mock payment was logged
        captured = capsys.readouterr()
        assert "mock_payment_processed" in captured.out
        assert "PAY-" in captured.out

    def test_rejected_invoice_logged(
        self, payment_agent: PaymentAgent, rejections_file: Path
    ) -> None:
        """Rejected invoice should be logged to rejections file."""
        invoice = make_invoice("INV-REJ-001")
        approval = make_approval(
            "INV-REJ-001",
            ApprovalStatus.REJECTED,
            "Stock exceeded",
        )
        state = make_state(invoice, approval)

        result = payment_agent.run(state)

        assert result.payment_status == "rejected"
        assert result.completed_at is not None

        # Check rejection was logged
        assert rejections_file.exists()
        content = rejections_file.read_text()
        assert "INV-REJ-001" in content
        assert "Stock exceeded" in content

    def test_escalated_invoice_queued(self, payment_agent: PaymentAgent) -> None:
        """Escalated invoice should be queued for review."""
        invoice = make_invoice("INV-ESC-001")
        approval = make_approval(
            "INV-ESC-001",
            ApprovalStatus.NEEDS_HUMAN,
            "Foreign currency",
        )
        state = make_state(invoice, approval)

        result = payment_agent.run(state)

        assert result.payment_status == "escalated"
        assert result.completed_at is not None

    def test_invoice_recorded_as_processed(
        self, payment_agent: PaymentAgent, test_db: Path
    ) -> None:
        """Processed invoice should be recorded for duplicate detection."""
        invoice = make_invoice("INV-REC-001")
        approval = make_approval("INV-REC-001", ApprovalStatus.APPROVED)
        state = make_state(invoice, approval)

        payment_agent.run(state)

        # Check it was recorded
        dup = check_duplicate_invoice("INV-REC-001", test_db)
        assert dup is not None
        assert dup["invoice_number"] == "INV-REC-001"
        assert dup["status"] == "paid"

    def test_rejected_invoice_recorded(
        self, payment_agent: PaymentAgent, test_db: Path
    ) -> None:
        """Rejected invoice should also be recorded."""
        invoice = make_invoice("INV-REC-002")
        approval = make_approval("INV-REC-002", ApprovalStatus.REJECTED)
        state = make_state(invoice, approval)

        payment_agent.run(state)

        dup = check_duplicate_invoice("INV-REC-002", test_db)
        assert dup is not None
        assert dup["status"] == "rejected"

    def test_missing_invoice_sets_error(self, payment_agent: PaymentAgent) -> None:
        """Missing invoice should set error."""
        state = PipelineState(
            run_id="test-run",
            source_path="test.txt",
            invoice=None,
            approval_decision=make_approval(),
        )

        result = payment_agent.run(state)

        assert result.error is not None
        assert result.error_stage == "payment"

    def test_missing_approval_sets_error(self, payment_agent: PaymentAgent) -> None:
        """Missing approval should set error."""
        state = PipelineState(
            run_id="test-run",
            source_path="test.txt",
            invoice=make_invoice(),
            approval_decision=None,
        )

        result = payment_agent.run(state)

        assert result.error is not None
        assert result.error_stage == "payment"


class TestMockPayment:
    """Tests for mock_payment function."""

    def test_returns_success(self) -> None:
        """Mock payment should return success."""
        result = mock_payment(
            vendor="Test Vendor",
            amount=500.0,
            invoice_number="INV-001",
        )

        assert result["status"] == "success"
        assert result["vendor"] == "Test Vendor"
        assert result["amount"] == 500.0
        assert result["invoice_number"] == "INV-001"

    def test_generates_reference(self) -> None:
        """Mock payment should generate unique reference."""
        result1 = mock_payment("A", 100.0, "INV-1")
        result2 = mock_payment("B", 200.0, "INV-2")

        assert result1["reference"] != result2["reference"]
        assert result1["reference"].startswith("PAY-")
        assert result2["reference"].startswith("PAY-")
