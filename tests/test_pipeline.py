"""End-to-end pipeline tests matching README scenarios."""

from pathlib import Path

import pytest

from src.db.queries import close_connection
from src.db.seed import seed_database
from src.graph.pipeline import run_pipeline
from src.llm.client import MockClient
from src.models.invoice import ApprovalStatus, ValidationFlag


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Create a fresh test database for each test."""
    db_path = tmp_path / "test.db"
    seed_database(db_path, reset=True)
    close_connection()
    return db_path


@pytest.fixture
def mock_client() -> MockClient:
    """Provide a mock LLM client."""
    return MockClient()


@pytest.fixture
def sample_invoices_dir() -> Path:
    """Path to sample invoices."""
    return Path(__file__).parent.parent / "data" / "invoices"


class TestReadmeScenarios:
    """
    Tests matching the scenarios from README.md:

    | Scenario | Invoice | What should happen |
    |---|---|---|
    | Normal order within stock | INV-1001, INV-1004, INV-1006 | passes validation |
    | Quantity exceeds stock | INV-1002 (20x GadgetX, only 5) | Flagged as stock mismatch |
    | Fraudulent / zero-stock | INV-1003 (FakeItem, 0 stock) | Flagged as out of stock or suspicious |
    | Item not in database | INV-1008, INV-1016 | Flagged as unknown item |
    | Invalid data | INV-1009 (negative quantity) | Flagged as data integrity issue |
    """

    def test_inv_1001_normal_approved(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """INV-1001: Normal order within stock should be approved."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1001.txt"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result.error is None
        assert result.invoice is not None
        assert result.validation_result is not None
        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.APPROVED
        assert result.payment_status == "success"

    def test_inv_1004_json_approved(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """INV-1004: Clean JSON invoice should be approved."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1004.json"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result.error is None
        assert result.invoice is not None
        assert result.invoice.invoice_number == "INV-1004"
        assert result.approval_decision.status == ApprovalStatus.APPROVED

    def test_inv_1002_stock_exceeded_rejected(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """INV-1002: Quantity exceeds stock should be rejected."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1002.txt"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result.error is None
        assert result.validation_result is not None

        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.STOCK_EXCEEDED in flags

        assert result.approval_decision is not None
        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_inv_1003_fraud_rejected(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """INV-1003: Fraudulent invoice should be rejected."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1003.txt"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result.error is None
        assert result.validation_result is not None

        flags = {f.flag for f in result.validation_result.flags}
        # Should have multiple fraud indicators
        assert ValidationFlag.ZERO_STOCK in flags or ValidationFlag.BLACKLISTED_VENDOR in flags
        assert ValidationFlag.FRAUD_SUSPECT in flags

        assert result.approval_decision.status == ApprovalStatus.REJECTED
        assert result.payment_status == "rejected"

    def test_inv_1008_unknown_items_rejected(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """INV-1008: Unknown items (SuperGizmo, MegaSprocket) should be rejected."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1008.txt"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result.error is None
        assert result.validation_result is not None

        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.UNKNOWN_ITEM in flags

        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_inv_1009_negative_qty_rejected(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """INV-1009: Negative quantity should be rejected."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1009.json"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result.error is None
        assert result.invoice is not None
        assert result.validation_result is not None

        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.NEGATIVE_QTY in flags
        assert ValidationFlag.MISSING_VENDOR in flags  # Empty vendor

        assert result.approval_decision.status == ApprovalStatus.REJECTED

    def test_inv_1016_unknown_item_rejected(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """INV-1016: WidgetC (unknown) should be rejected."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1016.json"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result.error is None
        assert result.validation_result is not None

        flags = {f.flag for f in result.validation_result.flags}
        assert ValidationFlag.UNKNOWN_ITEM in flags

        assert result.approval_decision.status == ApprovalStatus.REJECTED


class TestPipelineEdgeCases:
    """Additional edge case tests."""

    def test_pipeline_terminates_on_file_not_found(
        self, mock_client: MockClient, test_db: Path
    ) -> None:
        """Pipeline should terminate gracefully on missing file."""
        result = run_pipeline(
            "/nonexistent/invoice.txt",
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result.error is not None
        assert result.error_stage == "ingestion"
        assert result.invoice is None

    def test_pipeline_generates_unique_run_ids(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """Each pipeline run should have a unique run_id."""
        result1 = run_pipeline(
            str(sample_invoices_dir / "invoice_1001.txt"),
            llm_client=mock_client,
            db_path=str(test_db),
        )
        result2 = run_pipeline(
            str(sample_invoices_dir / "invoice_1001.txt"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result1.run_id != result2.run_id

    def test_pipeline_tracks_duration(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """Pipeline should track start and completion time."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1001.txt"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        assert result.started_at is not None
        assert result.completed_at is not None
        assert result.completed_at >= result.started_at

    def test_pipeline_collects_events(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """Pipeline should collect events from all agents."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1001.txt"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        # Should have events from all 4 agents
        agents = {e["agent"] for e in result.events}
        assert "ingestion" in agents
        assert "validation" in agents
        assert "approval" in agents
        assert "payment" in agents

    def test_pipeline_summary(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """Pipeline summary should contain key information."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1004.json"),
            llm_client=mock_client,
            db_path=str(test_db),
        )

        summary = result.to_summary()

        assert summary["run_id"] == result.run_id
        assert summary["invoice_number"] == "INV-1004"
        assert summary["vendor"] == "Precision Parts Ltd."
        assert summary["final_status"] == "success"
        assert summary["duration_ms"] is not None


class TestPipelineWithCustomRunId:
    """Tests for custom run ID support."""

    def test_custom_run_id_used(
        self, sample_invoices_dir: Path, mock_client: MockClient, test_db: Path
    ) -> None:
        """Custom run_id should be used if provided."""
        result = run_pipeline(
            str(sample_invoices_dir / "invoice_1001.txt"),
            llm_client=mock_client,
            db_path=str(test_db),
            run_id="custom-test-run-123",
        )

        assert result.run_id == "custom-test-run-123"
