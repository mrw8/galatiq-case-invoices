"""Tests for ingestion agent."""

from decimal import Decimal
from pathlib import Path

import pytest

from src.agents.ingestion import IngestionAgent, IngestionError
from src.llm.client import MockClient
from src.models.pipeline import PipelineState


@pytest.fixture
def mock_client() -> MockClient:
    """Provide a mock LLM client."""
    return MockClient()


@pytest.fixture
def ingestion_agent(mock_client: MockClient) -> IngestionAgent:
    """Create ingestion agent with mock client."""
    return IngestionAgent(mock_client)


@pytest.fixture
def sample_invoices_dir() -> Path:
    """Path to sample invoices."""
    return Path(__file__).parent.parent / "data" / "invoices"


def make_state(source_path: str) -> PipelineState:
    """Helper to create pipeline state."""
    return PipelineState(run_id="test-run", source_path=source_path)


class TestIngestionAgent:
    """Tests for IngestionAgent."""

    def test_json_direct_parse(
        self, ingestion_agent: IngestionAgent, sample_invoices_dir: Path
    ) -> None:
        """JSON files should be parsed directly without LLM."""
        state = make_state(str(sample_invoices_dir / "invoice_1004.json"))
        result = ingestion_agent.run(state)

        assert result.invoice is not None
        assert result.error is None
        assert result.invoice.invoice_number == "INV-1004"
        assert result.invoice.vendor.name == "Precision Parts Ltd."
        assert result.invoice.total == Decimal("1890")
        assert len(result.invoice.line_items) == 2

    def test_json_with_negative_qty(
        self, ingestion_agent: IngestionAgent, sample_invoices_dir: Path
    ) -> None:
        """JSON with negative quantity should parse correctly."""
        state = make_state(str(sample_invoices_dir / "invoice_1009.json"))
        result = ingestion_agent.run(state)

        assert result.invoice is not None
        # Check negative quantity is preserved
        widget_a = next(i for i in result.invoice.line_items if i.item == "WidgetA")
        assert widget_a.quantity == -5

    def test_json_with_empty_vendor(
        self, ingestion_agent: IngestionAgent, sample_invoices_dir: Path
    ) -> None:
        """JSON with empty vendor should parse correctly."""
        state = make_state(str(sample_invoices_dir / "invoice_1009.json"))
        result = ingestion_agent.run(state)

        assert result.invoice is not None
        assert result.invoice.vendor.name == ""
        assert result.invoice.vendor.is_empty

    def test_txt_file_loads(
        self, ingestion_agent: IngestionAgent, sample_invoices_dir: Path
    ) -> None:
        """TXT files should be loaded and processed."""
        state = make_state(str(sample_invoices_dir / "invoice_1001.txt"))
        result = ingestion_agent.run(state)

        # With mock client, we get mock response
        assert result.invoice is not None
        assert result.error is None
        assert result.raw_content is not None
        assert "Widgets Inc." in result.raw_content

    def test_csv_file_loads(
        self, ingestion_agent: IngestionAgent, sample_invoices_dir: Path
    ) -> None:
        """CSV files should be loaded."""
        state = make_state(str(sample_invoices_dir / "invoice_1006.csv"))
        result = ingestion_agent.run(state)

        assert result.invoice is not None
        assert result.raw_content is not None
        assert "INV-1006" in result.raw_content

    def test_file_not_found(self, ingestion_agent: IngestionAgent) -> None:
        """Missing file should set error."""
        state = make_state("/nonexistent/invoice.txt")
        result = ingestion_agent.run(state)

        assert result.invoice is None
        assert result.error is not None
        assert "not found" in result.error.lower()
        assert result.error_stage == "ingestion"

    def test_unsupported_format(
        self, ingestion_agent: IngestionAgent, tmp_path: Path
    ) -> None:
        """Unsupported file format should set error."""
        bad_file = tmp_path / "invoice.docx"
        bad_file.write_text("fake content")

        state = make_state(str(bad_file))
        result = ingestion_agent.run(state)

        assert result.invoice is None
        assert result.error is not None
        assert "unsupported" in result.error.lower()

    def test_invoice_number_normalization(
        self, ingestion_agent: IngestionAgent, sample_invoices_dir: Path
    ) -> None:
        """Invoice numbers should be normalized."""
        state = make_state(str(sample_invoices_dir / "invoice_1004.json"))
        result = ingestion_agent.run(state)

        assert result.invoice is not None
        # Should have INV- prefix
        assert result.invoice.invoice_number.startswith("INV-")

    def test_xml_file_loads(
        self, ingestion_agent: IngestionAgent, sample_invoices_dir: Path
    ) -> None:
        """XML files should be loaded."""
        state = make_state(str(sample_invoices_dir / "invoice_1014.xml"))
        result = ingestion_agent.run(state)

        assert result.invoice is not None
        assert result.raw_content is not None
        assert "INV-1014" in result.raw_content


class TestMockClientBehavior:
    """Tests for mock client response generation."""

    def test_mock_client_returns_parseable_json(self, mock_client: MockClient) -> None:
        """Mock client should return valid JSON."""
        import json

        messages = [
            {"role": "system", "content": "Extract invoice"},
            {"role": "user", "content": "Extract invoice from: INV-1001 test"},
        ]
        response = mock_client.chat(messages)

        # Should be valid JSON
        data = json.loads(response)
        assert "invoice_number" in data

    def test_mock_client_context_aware(self, mock_client: MockClient) -> None:
        """Mock client should adjust response based on context."""
        import json

        # Ask about invoice 1003
        messages = [
            {"role": "user", "content": "Extract invoice 1003 Fraudster"},
        ]
        response = mock_client.chat(messages)
        data = json.loads(response)

        # Should return fraud-related mock data
        assert "1003" in data.get("invoice_number", "")

    def test_mock_client_call_count(self, mock_client: MockClient) -> None:
        """Mock client should track call count."""
        assert mock_client.call_count == 0

        mock_client.chat([{"role": "user", "content": "test"}])
        assert mock_client.call_count == 1

        mock_client.chat([{"role": "user", "content": "test2"}])
        assert mock_client.call_count == 2

        mock_client.reset()
        assert mock_client.call_count == 0
