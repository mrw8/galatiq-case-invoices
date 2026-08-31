"""Human review queue for escalated invoices."""

import json
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ReviewStatus(str, Enum):
    """Status of a review item."""
    
    PENDING = "pending"        # Waiting for review
    ASSIGNED = "assigned"      # Assigned to a reviewer
    IN_REVIEW = "in_review"    # Actively being reviewed
    COMPLETED = "completed"    # Review finished
    EXPIRED = "expired"        # SLA breached, needs escalation


class ReviewAction(str, Enum):
    """Actions a reviewer can take."""
    
    APPROVE = "approve"
    REJECT = "reject"
    CORRECT = "correct"      # Approve with corrections
    ESCALATE = "escalate"    # Escalate to higher authority
    DEFER = "defer"          # Return to queue for later


class ReviewDecision(BaseModel):
    """A reviewer's decision on an item."""
    
    action: ReviewAction
    reviewer: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    notes: str | None = None
    corrections: dict[str, Any] | None = None  # Field corrections if action=CORRECT


class ReviewItem(BaseModel):
    """An item in the human review queue."""
    
    id: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    run_id: str
    invoice_number: str
    vendor: str
    amount: float
    currency: str = "USD"
    
    # Why it needs review
    escalation_reason: str
    escalation_flags: list[str] = Field(default_factory=list)
    
    # Review state
    status: ReviewStatus = ReviewStatus.PENDING
    priority: int = 0  # Higher = more urgent
    assigned_to: str | None = None
    assigned_at: datetime | None = None
    due_by: datetime | None = None  # SLA deadline
    
    # Original data for reference
    invoice_data: dict[str, Any] = Field(default_factory=dict)
    validation_flags: list[str] = Field(default_factory=list)
    
    # Decision
    decision: ReviewDecision | None = None


class ReviewQueue:
    """
    Queue for human review of escalated invoices.
    
    Usage:
        queue = ReviewQueue()
        
        # Add item for review
        queue.add(ReviewItem(
            run_id="run-123",
            invoice_number="INV-1001",
            vendor="Foreign Corp",
            amount=5000,
            currency="EUR",
            escalation_reason="Foreign currency requires manual approval",
        ))
        
        # Get pending items
        pending = queue.get_pending()
        
        # Claim an item
        queue.assign(item_id, "reviewer@example.com")
        
        # Submit decision
        queue.decide(item_id, ReviewDecision(
            action=ReviewAction.APPROVE,
            reviewer="reviewer@example.com",
            notes="Exchange rate verified",
        ))
    """
    
    def __init__(self, db_path: str | Path = "review_queue.db"):
        self.db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize review queue database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS review_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                run_id TEXT NOT NULL,
                invoice_number TEXT NOT NULL,
                vendor TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'USD',
                escalation_reason TEXT NOT NULL,
                escalation_flags TEXT,
                status TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                assigned_to TEXT,
                assigned_at TEXT,
                due_by TEXT,
                invoice_data TEXT,
                validation_flags TEXT,
                decision TEXT
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_review_status 
            ON review_items(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_review_assigned 
            ON review_items(assigned_to)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_review_priority 
            ON review_items(priority DESC, created_at ASC)
        """)
        
        conn.commit()
        conn.close()
    
    def add(self, item: ReviewItem) -> ReviewItem:
        """Add an item to the review queue."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        now = datetime.utcnow()
        
        cursor.execute("""
            INSERT INTO review_items
            (created_at, updated_at, run_id, invoice_number, vendor, amount,
             currency, escalation_reason, escalation_flags, status, priority,
             assigned_to, assigned_at, due_by, invoice_data, validation_flags, decision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.created_at.isoformat(),
            now.isoformat(),
            item.run_id,
            item.invoice_number,
            item.vendor,
            item.amount,
            item.currency,
            item.escalation_reason,
            json.dumps(item.escalation_flags),
            item.status.value,
            item.priority,
            item.assigned_to,
            item.assigned_at.isoformat() if item.assigned_at else None,
            item.due_by.isoformat() if item.due_by else None,
            json.dumps(item.invoice_data),
            json.dumps(item.validation_flags),
            None,
        ))
        
        item_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return ReviewItem(**{**item.model_dump(), "id": item_id, "updated_at": now})
    
    def get_pending(self, limit: int = 50) -> list[ReviewItem]:
        """Get pending items, ordered by priority and age."""
        return self._query(
            "status IN (?, ?)",
            (ReviewStatus.PENDING.value, ReviewStatus.ASSIGNED.value),
            limit=limit,
        )
    
    def get_assigned_to(self, reviewer: str) -> list[ReviewItem]:
        """Get items assigned to a specific reviewer."""
        return self._query(
            "assigned_to = ? AND status != ?",
            (reviewer, ReviewStatus.COMPLETED.value),
        )
    
    def get_by_invoice(self, invoice_number: str) -> ReviewItem | None:
        """Get review item for an invoice."""
        items = self._query("invoice_number = ?", (invoice_number,), limit=1)
        return items[0] if items else None
    
    def assign(self, item_id: int, reviewer: str) -> ReviewItem | None:
        """Assign an item to a reviewer."""
        item = self._get_by_id(item_id)
        if not item:
            return None
        
        now = datetime.utcnow()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE review_items SET
                status = ?,
                assigned_to = ?,
                assigned_at = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            ReviewStatus.ASSIGNED.value,
            reviewer,
            now.isoformat(),
            now.isoformat(),
            item_id,
        ))
        
        conn.commit()
        conn.close()
        
        return self._get_by_id(item_id)
    
    def unassign(self, item_id: int) -> ReviewItem | None:
        """Return an item to the queue."""
        item = self._get_by_id(item_id)
        if not item:
            return None
        
        now = datetime.utcnow()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE review_items SET
                status = ?,
                assigned_to = NULL,
                assigned_at = NULL,
                updated_at = ?
            WHERE id = ?
        """, (ReviewStatus.PENDING.value, now.isoformat(), item_id))
        
        conn.commit()
        conn.close()
        
        return self._get_by_id(item_id)
    
    def decide(self, item_id: int, decision: ReviewDecision) -> ReviewItem | None:
        """Record a review decision."""
        item = self._get_by_id(item_id)
        if not item:
            return None
        
        now = datetime.utcnow()
        
        # Handle defer action - return to queue
        if decision.action == ReviewAction.DEFER:
            return self.unassign(item_id)
        
        # Handle escalate - increase priority and unassign
        if decision.action == ReviewAction.ESCALATE:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE review_items SET
                    status = ?,
                    priority = priority + 10,
                    assigned_to = NULL,
                    assigned_at = NULL,
                    updated_at = ?,
                    decision = ?
                WHERE id = ?
            """, (
                ReviewStatus.PENDING.value,
                now.isoformat(),
                json.dumps(decision.model_dump(), default=str),
                item_id,
            ))
            
            conn.commit()
            conn.close()
            
            return self._get_by_id(item_id)
        
        # Normal decision (approve, reject, correct)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE review_items SET
                status = ?,
                updated_at = ?,
                decision = ?
            WHERE id = ?
        """, (
            ReviewStatus.COMPLETED.value,
            now.isoformat(),
            json.dumps(decision.model_dump(), default=str),
            item_id,
        ))
        
        conn.commit()
        conn.close()
        
        return self._get_by_id(item_id)
    
    def get_stats(self) -> dict[str, Any]:
        """Get queue statistics."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Counts by status
        cursor.execute("""
            SELECT status, COUNT(*) FROM review_items GROUP BY status
        """)
        by_status = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Counts by reviewer
        cursor.execute("""
            SELECT assigned_to, COUNT(*) FROM review_items 
            WHERE assigned_to IS NOT NULL AND status != 'completed'
            GROUP BY assigned_to
        """)
        by_reviewer = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Average time to decision (for completed items)
        cursor.execute("""
            SELECT AVG(
                julianday(updated_at) - julianday(created_at)
            ) * 24 * 60 as avg_minutes
            FROM review_items
            WHERE status = 'completed'
        """)
        avg_minutes = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "by_status": by_status,
            "by_reviewer": by_reviewer,
            "avg_resolution_minutes": round(avg_minutes, 1) if avg_minutes else None,
            "total_pending": by_status.get("pending", 0) + by_status.get("assigned", 0),
        }
    
    def _get_by_id(self, item_id: int) -> ReviewItem | None:
        """Get item by ID."""
        items = self._query("id = ?", (item_id,), limit=1)
        return items[0] if items else None
    
    def _query(
        self,
        where: str,
        params: tuple,
        limit: int = 100,
    ) -> list[ReviewItem]:
        """Execute a query and return items."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute(f"""
            SELECT * FROM review_items
            WHERE {where}
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
        """, (*params, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_item(row) for row in rows]
    
    def _row_to_item(self, row: sqlite3.Row) -> ReviewItem:
        """Convert database row to ReviewItem."""
        decision_data = json.loads(row["decision"]) if row["decision"] else None
        decision = None
        if decision_data:
            decision = ReviewDecision(
                action=ReviewAction(decision_data["action"]),
                reviewer=decision_data["reviewer"],
                timestamp=datetime.fromisoformat(decision_data["timestamp"]),
                notes=decision_data.get("notes"),
                corrections=decision_data.get("corrections"),
            )
        
        return ReviewItem(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            run_id=row["run_id"],
            invoice_number=row["invoice_number"],
            vendor=row["vendor"],
            amount=row["amount"],
            currency=row["currency"],
            escalation_reason=row["escalation_reason"],
            escalation_flags=json.loads(row["escalation_flags"]) if row["escalation_flags"] else [],
            status=ReviewStatus(row["status"]),
            priority=row["priority"],
            assigned_to=row["assigned_to"],
            assigned_at=datetime.fromisoformat(row["assigned_at"]) if row["assigned_at"] else None,
            due_by=datetime.fromisoformat(row["due_by"]) if row["due_by"] else None,
            invoice_data=json.loads(row["invoice_data"]) if row["invoice_data"] else {},
            validation_flags=json.loads(row["validation_flags"]) if row["validation_flags"] else [],
            decision=decision,
        )
