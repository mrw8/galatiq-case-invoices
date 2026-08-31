"""Immutable audit trail for compliance and debugging."""

import json
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AuditAction(str, Enum):
    """Types of auditable actions."""
    
    # Pipeline actions
    INVOICE_RECEIVED = "invoice_received"
    INVOICE_PARSED = "invoice_parsed"
    INVOICE_VALIDATED = "invoice_validated"
    INVOICE_APPROVED = "invoice_approved"
    INVOICE_REJECTED = "invoice_rejected"
    INVOICE_ESCALATED = "invoice_escalated"
    PAYMENT_PROCESSED = "payment_processed"
    PAYMENT_FAILED = "payment_failed"
    
    # Human actions
    HUMAN_APPROVED = "human_approved"
    HUMAN_REJECTED = "human_rejected"
    HUMAN_CORRECTED = "human_corrected"
    
    # System actions
    RETRY_ATTEMPTED = "retry_attempted"
    ERROR_OCCURRED = "error_occurred"
    ERROR_RECOVERED = "error_recovered"


class AuditEvent(BaseModel, frozen=True):
    """A single audit event - immutable once created."""
    
    id: int | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    run_id: str
    invoice_number: str | None = None
    action: AuditAction
    actor: str = "system"  # "system", "user:john@example.com", "agent:validation"
    details: dict[str, Any] = Field(default_factory=dict)
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None


class AuditTrail:
    """
    Append-only audit trail storage.
    
    Provides immutable logging of all invoice processing events
    for compliance, debugging, and analytics.
    """
    
    def __init__(self, db_path: str | Path = "audit.db"):
        self.db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize audit database with append-only table."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Audit events table - append only, no updates/deletes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                run_id TEXT NOT NULL,
                invoice_number TEXT,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                details TEXT,
                before_state TEXT,
                after_state TEXT
            )
        """)
        
        # Indexes for common queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_run_id ON audit_events(run_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_invoice ON audit_events(invoice_number)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_events(action)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp)
        """)
        
        conn.commit()
        conn.close()
    
    def record(self, event: AuditEvent) -> AuditEvent:
        """
        Record an audit event. Returns event with ID populated.
        
        This is append-only - events cannot be modified or deleted.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO audit_events 
            (timestamp, run_id, invoice_number, action, actor, details, before_state, after_state)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.timestamp.isoformat(),
            event.run_id,
            event.invoice_number,
            event.action.value,
            event.actor,
            json.dumps(event.details) if event.details else None,
            json.dumps(event.before_state) if event.before_state else None,
            json.dumps(event.after_state) if event.after_state else None,
        ))
        
        event_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # Return new event with ID (Pydantic frozen, so create new)
        return AuditEvent(
            id=event_id,
            timestamp=event.timestamp,
            run_id=event.run_id,
            invoice_number=event.invoice_number,
            action=event.action,
            actor=event.actor,
            details=event.details,
            before_state=event.before_state,
            after_state=event.after_state,
        )
    
    def get_by_run(self, run_id: str) -> list[AuditEvent]:
        """Get all events for a specific run."""
        return self._query("run_id = ?", (run_id,))
    
    def get_by_invoice(self, invoice_number: str) -> list[AuditEvent]:
        """Get all events for a specific invoice."""
        return self._query("invoice_number = ?", (invoice_number,))
    
    def get_by_action(self, action: AuditAction, limit: int = 100) -> list[AuditEvent]:
        """Get recent events of a specific action type."""
        return self._query("action = ?", (action.value,), limit=limit)
    
    def get_recent(self, limit: int = 100) -> list[AuditEvent]:
        """Get most recent events."""
        return self._query("1=1", (), limit=limit, order="DESC")
    
    def get_errors(self, since: datetime | None = None, limit: int = 100) -> list[AuditEvent]:
        """Get error events, optionally since a timestamp."""
        if since:
            return self._query(
                "action = ? AND timestamp >= ?",
                (AuditAction.ERROR_OCCURRED.value, since.isoformat()),
                limit=limit,
            )
        return self._query("action = ?", (AuditAction.ERROR_OCCURRED.value,), limit=limit)
    
    def _query(
        self,
        where: str,
        params: tuple,
        limit: int = 100,
        order: str = "ASC",
    ) -> list[AuditEvent]:
        """Execute a query and return events."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT * FROM audit_events
            WHERE {where}
            ORDER BY timestamp {order}
            LIMIT ?
        """, (*params, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        events = []
        for row in rows:
            events.append(AuditEvent(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                run_id=row["run_id"],
                invoice_number=row["invoice_number"],
                action=AuditAction(row["action"]),
                actor=row["actor"],
                details=json.loads(row["details"]) if row["details"] else {},
                before_state=json.loads(row["before_state"]) if row["before_state"] else None,
                after_state=json.loads(row["after_state"]) if row["after_state"] else None,
            ))
        
        return events
    
    def count_by_action(self, since: datetime | None = None) -> dict[str, int]:
        """Get counts of events by action type."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if since:
            cursor.execute("""
                SELECT action, COUNT(*) as count
                FROM audit_events
                WHERE timestamp >= ?
                GROUP BY action
            """, (since.isoformat(),))
        else:
            cursor.execute("""
                SELECT action, COUNT(*) as count
                FROM audit_events
                GROUP BY action
            """)
        
        rows = cursor.fetchall()
        conn.close()
        
        return {row[0]: row[1] for row in rows}


# Convenience function for pipeline integration
def audit(
    action: AuditAction,
    run_id: str,
    invoice_number: str | None = None,
    actor: str = "system",
    details: dict | None = None,
    before_state: dict | None = None,
    after_state: dict | None = None,
    trail: AuditTrail | None = None,
) -> AuditEvent:
    """
    Record an audit event. Convenience function for pipeline use.
    
    Usage:
        audit(AuditAction.INVOICE_APPROVED, run_id, invoice_number="INV-1001")
    """
    if trail is None:
        trail = AuditTrail()
    
    event = AuditEvent(
        run_id=run_id,
        invoice_number=invoice_number,
        action=action,
        actor=actor,
        details=details or {},
        before_state=before_state,
        after_state=after_state,
    )
    
    return trail.record(event)
