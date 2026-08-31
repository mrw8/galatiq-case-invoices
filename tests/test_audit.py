"""Tests for audit trail."""

from datetime import datetime
from pathlib import Path

import pytest

from src.audit.trail import AuditAction, AuditEvent, AuditTrail, audit


@pytest.fixture
def audit_trail(tmp_path: Path) -> AuditTrail:
    """Create a test audit trail."""
    return AuditTrail(tmp_path / "test_audit.db")


class TestAuditTrail:
    """Tests for AuditTrail."""

    def test_record_event(self, audit_trail: AuditTrail) -> None:
        """Should record an event and return it with ID."""
        event = AuditEvent(
            run_id="run-123",
            invoice_number="INV-1001",
            action=AuditAction.INVOICE_RECEIVED,
            actor="system",
        )
        
        recorded = audit_trail.record(event)
        
        assert recorded.id is not None
        assert recorded.run_id == "run-123"
        assert recorded.invoice_number == "INV-1001"
        assert recorded.action == AuditAction.INVOICE_RECEIVED

    def test_get_by_run(self, audit_trail: AuditTrail) -> None:
        """Should retrieve events by run ID."""
        # Record events for two runs
        audit_trail.record(AuditEvent(
            run_id="run-1", action=AuditAction.INVOICE_RECEIVED
        ))
        audit_trail.record(AuditEvent(
            run_id="run-1", action=AuditAction.INVOICE_APPROVED
        ))
        audit_trail.record(AuditEvent(
            run_id="run-2", action=AuditAction.INVOICE_RECEIVED
        ))
        
        run1_events = audit_trail.get_by_run("run-1")
        
        assert len(run1_events) == 2
        assert all(e.run_id == "run-1" for e in run1_events)

    def test_get_by_invoice(self, audit_trail: AuditTrail) -> None:
        """Should retrieve events by invoice number."""
        audit_trail.record(AuditEvent(
            run_id="run-1",
            invoice_number="INV-1001",
            action=AuditAction.INVOICE_RECEIVED,
        ))
        audit_trail.record(AuditEvent(
            run_id="run-2",
            invoice_number="INV-1001",
            action=AuditAction.INVOICE_APPROVED,
        ))
        
        events = audit_trail.get_by_invoice("INV-1001")
        
        assert len(events) == 2
        assert all(e.invoice_number == "INV-1001" for e in events)

    def test_get_by_action(self, audit_trail: AuditTrail) -> None:
        """Should retrieve events by action type."""
        audit_trail.record(AuditEvent(
            run_id="run-1", action=AuditAction.INVOICE_APPROVED
        ))
        audit_trail.record(AuditEvent(
            run_id="run-2", action=AuditAction.INVOICE_REJECTED
        ))
        audit_trail.record(AuditEvent(
            run_id="run-3", action=AuditAction.INVOICE_APPROVED
        ))
        
        approvals = audit_trail.get_by_action(AuditAction.INVOICE_APPROVED)
        
        assert len(approvals) == 2

    def test_event_with_details(self, audit_trail: AuditTrail) -> None:
        """Should store and retrieve event details."""
        event = AuditEvent(
            run_id="run-1",
            action=AuditAction.PAYMENT_PROCESSED,
            details={"amount": 1000, "reference": "PAY-123"},
        )
        
        recorded = audit_trail.record(event)
        retrieved = audit_trail.get_by_run("run-1")[0]
        
        assert retrieved.details["amount"] == 1000
        assert retrieved.details["reference"] == "PAY-123"

    def test_event_with_state_changes(self, audit_trail: AuditTrail) -> None:
        """Should store before and after state."""
        event = AuditEvent(
            run_id="run-1",
            action=AuditAction.HUMAN_CORRECTED,
            before_state={"vendor": "Acme Inc"},
            after_state={"vendor": "Acme Corporation"},
        )
        
        recorded = audit_trail.record(event)
        retrieved = audit_trail.get_by_run("run-1")[0]
        
        assert retrieved.before_state["vendor"] == "Acme Inc"
        assert retrieved.after_state["vendor"] == "Acme Corporation"

    def test_count_by_action(self, audit_trail: AuditTrail) -> None:
        """Should count events by action type."""
        for _ in range(3):
            audit_trail.record(AuditEvent(
                run_id="run", action=AuditAction.INVOICE_APPROVED
            ))
        for _ in range(2):
            audit_trail.record(AuditEvent(
                run_id="run", action=AuditAction.INVOICE_REJECTED
            ))
        
        counts = audit_trail.count_by_action()
        
        assert counts["invoice_approved"] == 3
        assert counts["invoice_rejected"] == 2


class TestAuditConvenienceFunction:
    """Tests for the audit() convenience function."""

    def test_audit_function(self, tmp_path: Path) -> None:
        """Should record event via convenience function."""
        trail = AuditTrail(tmp_path / "test.db")
        
        event = audit(
            AuditAction.INVOICE_RECEIVED,
            run_id="run-123",
            invoice_number="INV-1001",
            trail=trail,
        )
        
        assert event.id is not None
        assert event.action == AuditAction.INVOICE_RECEIVED
