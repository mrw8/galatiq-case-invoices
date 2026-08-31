"""Pytest fixtures for invoice processing tests."""

import tempfile
from pathlib import Path

import pytest

from src.db.seed import seed_database
from src.llm.client import MockClient


@pytest.fixture
def mock_client() -> MockClient:
    """Provide a mock LLM client for testing."""
    return MockClient()


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary database for testing."""
    db_path = tmp_path / "test_inventory.db"
    seed_database(db_path, reset=True)
    return db_path


@pytest.fixture
def sample_invoices_dir() -> Path:
    """Path to sample invoices directory."""
    return Path(__file__).parent.parent / "data" / "invoices"


@pytest.fixture
def invoice_1001_path(sample_invoices_dir: Path) -> Path:
    """Path to clean invoice 1001."""
    return sample_invoices_dir / "invoice_1001.txt"


@pytest.fixture
def invoice_1002_path(sample_invoices_dir: Path) -> Path:
    """Path to stock-exceeded invoice 1002."""
    return sample_invoices_dir / "invoice_1002.txt"


@pytest.fixture
def invoice_1003_path(sample_invoices_dir: Path) -> Path:
    """Path to fraud invoice 1003."""
    return sample_invoices_dir / "invoice_1003.txt"


@pytest.fixture
def invoice_1009_path(sample_invoices_dir: Path) -> Path:
    """Path to negative-qty invoice 1009."""
    return sample_invoices_dir / "invoice_1009.json"


@pytest.fixture
def invoice_1016_path(sample_invoices_dir: Path) -> Path:
    """Path to unknown-item invoice 1016."""
    return sample_invoices_dir / "invoice_1016.json"
