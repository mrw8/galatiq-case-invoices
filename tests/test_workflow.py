"""Tests for human review workflow."""

from pathlib import Path

import pytest

from src.workflow.review import (
    ReviewAction,
    ReviewDecision,
    ReviewItem,
    ReviewQueue,
    ReviewStatus,
)


@pytest.fixture
def queue(tmp_path: Path) -> ReviewQueue:
    """Create a test review queue."""
    return ReviewQueue(tmp_path / "test_queue.db")


class TestReviewQueue:
    """Tests for ReviewQueue."""

    def test_add_item(self, queue: ReviewQueue) -> None:
        """Should add an item to the queue."""
        item = ReviewItem(
            run_id="run-123",
            invoice_number="INV-1001",
            vendor="Foreign Corp",
            amount=5000.0,
            currency="EUR",
            escalation_reason="Foreign currency",
        )
        
        added = queue.add(item)
        
        assert added.id is not None
        assert added.status == ReviewStatus.PENDING

    def test_get_pending(self, queue: ReviewQueue) -> None:
        """Should get pending items."""
        queue.add(ReviewItem(
            run_id="run-1",
            invoice_number="INV-1001",
            vendor="A",
            amount=100,
            escalation_reason="Test",
        ))
        queue.add(ReviewItem(
            run_id="run-2",
            invoice_number="INV-1002",
            vendor="B",
            amount=200,
            escalation_reason="Test",
        ))
        
        pending = queue.get_pending()
        
        assert len(pending) == 2

    def test_assign_item(self, queue: ReviewQueue) -> None:
        """Should assign item to reviewer."""
        item = queue.add(ReviewItem(
            run_id="run-1",
            invoice_number="INV-1001",
            vendor="A",
            amount=100,
            escalation_reason="Test",
        ))
        
        assigned = queue.assign(item.id, "reviewer@example.com")
        
        assert assigned.status == ReviewStatus.ASSIGNED
        assert assigned.assigned_to == "reviewer@example.com"
        assert assigned.assigned_at is not None

    def test_unassign_item(self, queue: ReviewQueue) -> None:
        """Should return item to queue."""
        item = queue.add(ReviewItem(
            run_id="run-1",
            invoice_number="INV-1001",
            vendor="A",
            amount=100,
            escalation_reason="Test",
        ))
        queue.assign(item.id, "reviewer@example.com")
        
        unassigned = queue.unassign(item.id)
        
        assert unassigned.status == ReviewStatus.PENDING
        assert unassigned.assigned_to is None

    def test_approve_decision(self, queue: ReviewQueue) -> None:
        """Should record approval decision."""
        item = queue.add(ReviewItem(
            run_id="run-1",
            invoice_number="INV-1001",
            vendor="A",
            amount=100,
            escalation_reason="Test",
        ))
        queue.assign(item.id, "reviewer@example.com")
        
        decided = queue.decide(item.id, ReviewDecision(
            action=ReviewAction.APPROVE,
            reviewer="reviewer@example.com",
            notes="Verified exchange rate",
        ))
        
        assert decided.status == ReviewStatus.COMPLETED
        assert decided.decision.action == ReviewAction.APPROVE

    def test_reject_decision(self, queue: ReviewQueue) -> None:
        """Should record rejection decision."""
        item = queue.add(ReviewItem(
            run_id="run-1",
            invoice_number="INV-1001",
            vendor="A",
            amount=100,
            escalation_reason="Test",
        ))
        
        decided = queue.decide(item.id, ReviewDecision(
            action=ReviewAction.REJECT,
            reviewer="reviewer@example.com",
            notes="Suspicious vendor",
        ))
        
        assert decided.status == ReviewStatus.COMPLETED
        assert decided.decision.action == ReviewAction.REJECT

    def test_escalate_increases_priority(self, queue: ReviewQueue) -> None:
        """Should increase priority on escalation."""
        item = queue.add(ReviewItem(
            run_id="run-1",
            invoice_number="INV-1001",
            vendor="A",
            amount=100,
            priority=0,
            escalation_reason="Test",
        ))
        
        escalated = queue.decide(item.id, ReviewDecision(
            action=ReviewAction.ESCALATE,
            reviewer="reviewer@example.com",
            notes="Need manager approval",
        ))
        
        assert escalated.status == ReviewStatus.PENDING
        assert escalated.priority > 0

    def test_defer_returns_to_queue(self, queue: ReviewQueue) -> None:
        """Should return deferred item to queue."""
        item = queue.add(ReviewItem(
            run_id="run-1",
            invoice_number="INV-1001",
            vendor="A",
            amount=100,
            escalation_reason="Test",
        ))
        queue.assign(item.id, "reviewer@example.com")
        
        deferred = queue.decide(item.id, ReviewDecision(
            action=ReviewAction.DEFER,
            reviewer="reviewer@example.com",
        ))
        
        assert deferred.status == ReviewStatus.PENDING
        assert deferred.assigned_to is None

    def test_get_by_invoice(self, queue: ReviewQueue) -> None:
        """Should find item by invoice number."""
        queue.add(ReviewItem(
            run_id="run-1",
            invoice_number="INV-1001",
            vendor="A",
            amount=100,
            escalation_reason="Test",
        ))
        
        found = queue.get_by_invoice("INV-1001")
        
        assert found is not None
        assert found.invoice_number == "INV-1001"

    def test_get_stats(self, queue: ReviewQueue) -> None:
        """Should return queue statistics."""
        queue.add(ReviewItem(
            run_id="run-1",
            invoice_number="INV-1001",
            vendor="A",
            amount=100,
            escalation_reason="Test",
        ))
        
        stats = queue.get_stats()
        
        assert stats["total_pending"] >= 1
