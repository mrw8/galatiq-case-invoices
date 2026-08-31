"""Tests for error recovery."""

from pathlib import Path

import pytest

from src.recovery.handler import (
    FailedOperation,
    RecoveryHandler,
    RecoveryStatus,
    RetryPolicy,
)


@pytest.fixture
def handler(tmp_path: Path) -> RecoveryHandler:
    """Create a test recovery handler."""
    return RecoveryHandler(
        tmp_path / "test_recovery.db",
        policy=RetryPolicy(max_retries=2, initial_delay_seconds=0.1),
    )


class TestRetryPolicy:
    """Tests for RetryPolicy."""

    def test_delay_calculation(self) -> None:
        """Should calculate exponential backoff."""
        policy = RetryPolicy(
            initial_delay_seconds=1.0,
            backoff_multiplier=2.0,
            max_delay_seconds=10.0,
        )
        
        assert policy.get_delay(0) == 1.0
        assert policy.get_delay(1) == 2.0
        assert policy.get_delay(2) == 4.0
        assert policy.get_delay(3) == 8.0
        assert policy.get_delay(4) == 10.0  # Capped at max


class TestRecoveryHandler:
    """Tests for RecoveryHandler."""

    def test_record_failure(self, handler: RecoveryHandler) -> None:
        """Should record a failed operation."""
        op = handler.record_failure(
            run_id="run-123",
            stage="payment",
            error=ValueError("Payment failed"),
            invoice_number="INV-1001",
            context={"amount": 1000},
        )
        
        assert op.id is not None
        assert op.status == RecoveryStatus.PENDING
        assert op.error_type == "ValueError"
        assert "Payment failed" in op.error_message
        assert op.next_retry_at is not None

    def test_get_pending_retries(self, handler: RecoveryHandler) -> None:
        """Should get operations ready for retry."""
        handler.record_failure("run-1", "payment", "Error 1")
        handler.record_failure("run-2", "payment", "Error 2")
        
        # Wait a tiny bit for next_retry_at to pass
        import time
        time.sleep(0.15)
        
        pending = handler.get_pending_retries()
        
        assert len(pending) == 2

    def test_process_retries_success(self, handler: RecoveryHandler) -> None:
        """Should mark operations as recovered on success."""
        handler.record_failure("run-1", "payment", "Error")
        
        import time
        time.sleep(0.15)
        
        results = handler.process_pending_retries(lambda op: True)
        
        assert results["recovered"] == 1
        assert results["dead_letter"] == 0

    def test_process_retries_to_dead_letter(self, handler: RecoveryHandler) -> None:
        """Should move to dead letter after max retries."""
        # Record failure
        handler.record_failure("run-1", "payment", "Error")
        
        import time
        
        # Process retries until dead letter (max_retries=2)
        for _ in range(3):
            time.sleep(0.15)
            handler.process_pending_retries(lambda op: False)
        
        dead = handler.get_dead_letters()
        
        assert len(dead) == 1
        assert dead[0].retry_count >= 2

    def test_mark_resolved_manually(self, handler: RecoveryHandler) -> None:
        """Should allow manual resolution."""
        op = handler.record_failure("run-1", "payment", "Error")
        
        resolved = handler.mark_resolved(op.id, "Fixed manually")
        
        assert resolved.status == RecoveryStatus.RECOVERED
        assert resolved.resolution_notes == "Fixed manually"

    def test_get_stats(self, handler: RecoveryHandler) -> None:
        """Should return status counts."""
        handler.record_failure("run-1", "payment", "Error 1")
        handler.record_failure("run-2", "payment", "Error 2")
        
        stats = handler.get_stats()
        
        assert stats.get("pending", 0) == 2
