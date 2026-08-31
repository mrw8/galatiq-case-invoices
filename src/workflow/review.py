"""Human review queue for escalated invoices with role-based access."""

import json
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ApprovalLevel(int, Enum):
    """Approval authority levels (higher = more authority)."""
    
    L1 = 1  # Junior approver - handles routine escalations
    L2 = 2  # Senior approver - handles L1 escalations
    L3 = 3  # Manager - handles complex cases
    ADMIN = 99  # Admin - can see/handle everything


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


class Reviewer(BaseModel):
    """A user who can review escalated invoices."""
    
    id: int | None = None
    username: str
    display_name: str
    level: ApprovalLevel = ApprovalLevel.L1
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewDecision(BaseModel):
    """A reviewer's decision on an item."""
    
    action: ReviewAction
    reviewer: str  # username
    reviewer_level: ApprovalLevel | None = None
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
    current_level: ApprovalLevel = ApprovalLevel.L1  # Which level should handle this
    assigned_to: str | None = None  # username of assigned reviewer
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
        """Initialize review queue database with role-based access."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Reviewers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reviewers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                level INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        
        # Review items table with current_level
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
                current_level INTEGER DEFAULT 1,
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
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_review_level 
            ON review_items(current_level, status)
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
             current_level, assigned_to, assigned_at, due_by, invoice_data, 
             validation_flags, decision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            item.current_level.value,
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
    
    # ========== Reviewer Management ==========
    
    def add_reviewer(self, reviewer: Reviewer) -> Reviewer:
        """Add a reviewer to the system."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO reviewers (username, display_name, level, is_active, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            reviewer.username,
            reviewer.display_name,
            reviewer.level.value,
            1 if reviewer.is_active else 0,
            reviewer.created_at.isoformat(),
        ))
        
        reviewer_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return Reviewer(**{**reviewer.model_dump(), "id": reviewer_id})
    
    def get_reviewer(self, username: str) -> Reviewer | None:
        """Get a reviewer by username."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM reviewers WHERE username = ?",
            (username,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return Reviewer(
            id=row["id"],
            username=row["username"],
            display_name=row["display_name"],
            level=ApprovalLevel(row["level"]),
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
    
    def get_or_create_reviewer(self, username: str, display_name: str | None = None, 
                                level: ApprovalLevel = ApprovalLevel.L1) -> Reviewer:
        """Get existing reviewer or create/update one.
        
        If reviewer exists but level/display_name changed, updates them.
        """
        existing = self.get_reviewer(username)
        if existing:
            # Check if we need to update level or display name
            needs_update = (
                existing.level != level or 
                (display_name and existing.display_name != display_name)
            )
            if needs_update:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "UPDATE reviewers SET level = ?, display_name = ? WHERE username = ?",
                    (level.value, display_name or existing.display_name, username)
                )
                conn.commit()
                conn.close()
                # Return updated reviewer
                return Reviewer(
                    id=existing.id,
                    username=username,
                    display_name=display_name or existing.display_name,
                    level=level,
                    is_active=existing.is_active,
                )
            return existing
        
        return self.add_reviewer(Reviewer(
            username=username,
            display_name=display_name or username,
            level=level,
        ))
    
    def list_reviewers(self, active_only: bool = True) -> list[Reviewer]:
        """List all reviewers."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        where = "WHERE is_active = 1" if active_only else ""
        cursor = conn.execute(f"SELECT * FROM reviewers {where} ORDER BY level, username")
        rows = cursor.fetchall()
        conn.close()
        
        return [
            Reviewer(
                id=row["id"],
                username=row["username"],
                display_name=row["display_name"],
                level=ApprovalLevel(row["level"]),
                is_active=bool(row["is_active"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]
    
    # ========== Queue Methods ==========
    
    def get_pending(self, limit: int = 50, level: ApprovalLevel | None = None) -> list[ReviewItem]:
        """Get pending items, optionally filtered by approval level."""
        if level is not None:
            return self._query(
                "status IN (?, ?) AND current_level = ?",
                (ReviewStatus.PENDING.value, ReviewStatus.ASSIGNED.value, level.value),
                limit=limit,
            )
        return self._query(
            "status IN (?, ?)",
            (ReviewStatus.PENDING.value, ReviewStatus.ASSIGNED.value),
            limit=limit,
        )
    
    def get_for_reviewer(self, reviewer: Reviewer) -> list[ReviewItem]:
        """Get items this reviewer can see (at their level, unassigned or assigned to them)."""
        if reviewer.level == ApprovalLevel.ADMIN:
            # Admins see everything
            return self._query(
                "status IN (?, ?)",
                (ReviewStatus.PENDING.value, ReviewStatus.ASSIGNED.value),
            )
        return self._query(
            "status IN (?, ?) AND current_level = ? AND (assigned_to IS NULL OR assigned_to = ?)",
            (ReviewStatus.PENDING.value, ReviewStatus.ASSIGNED.value, 
             reviewer.level.value, reviewer.username),
        )
    
    def get_my_claimed(self, username: str) -> list[ReviewItem]:
        """Get items claimed by this reviewer (assigned to them)."""
        return self._query(
            "assigned_to = ? AND status = ?",
            (username, ReviewStatus.ASSIGNED.value),
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
    
    def decide(self, item_id: int, decision: ReviewDecision, 
               inventory_db_path: str | None = None,
               audit_db_path: str | None = None) -> ReviewItem | None:
        """Record a review decision.
        
        Args:
            item_id: Review item ID
            decision: The review decision
            inventory_db_path: Optional path to inventory DB to update processed_invoices status
            audit_db_path: Optional path to audit DB to record the decision
        """
        item = self._get_by_id(item_id)
        if not item:
            return None
        
        now = datetime.utcnow()
        
        # Handle defer action - return to queue
        if decision.action == ReviewAction.DEFER:
            return self.unassign(item_id)
        
        # Handle escalate - bump to next level, increase priority, unassign
        if decision.action == ReviewAction.ESCALATE:
            # Determine next level
            next_level = self._get_next_level(item.current_level)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE review_items SET
                    status = ?,
                    priority = priority + 10,
                    current_level = ?,
                    assigned_to = NULL,
                    assigned_at = NULL,
                    updated_at = ?,
                    decision = ?
                WHERE id = ?
            """, (
                ReviewStatus.PENDING.value,
                next_level.value,
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
        
        # Update processed_invoices status if inventory DB provided
        if inventory_db_path and decision.action == ReviewAction.REJECT:
            self._update_processed_invoice_status(item, "rejected_after_review", inventory_db_path)
        
        # Record audit event if audit DB provided
        if audit_db_path:
            self._record_audit_event(item, decision, audit_db_path)
        
        return self._get_by_id(item_id)
    
    def _update_processed_invoice_status(self, item: ReviewItem, status: str, db_path: str) -> None:
        """Update the status of a processed invoice."""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE processed_invoices SET status = ? WHERE run_id = ?",
            (status, item.run_id)
        )
        conn.commit()
        conn.close()
    
    def _record_audit_event(self, item: ReviewItem, decision: ReviewDecision, audit_db_path: str) -> None:
        """Record human review decision to audit trail."""
        from src.audit.trail import AuditTrail, AuditAction, AuditEvent
        
        audit = AuditTrail(audit_db_path)
        
        if decision.action == ReviewAction.APPROVE:
            action = AuditAction.HUMAN_APPROVED
        elif decision.action == ReviewAction.REJECT:
            action = AuditAction.HUMAN_REJECTED
        elif decision.action == ReviewAction.CORRECT:
            action = AuditAction.HUMAN_CORRECTED
        else:
            return  # Don't record escalate/defer
        
        audit.record(AuditEvent(
            run_id=item.run_id,
            invoice_number=item.invoice_number,
            action=action,
            actor=decision.reviewer,
            details={
                "reviewer_level": decision.reviewer_level.value if decision.reviewer_level else None,
                "notes": decision.notes,
            },
        ))
    
    def _get_next_level(self, current: ApprovalLevel) -> ApprovalLevel:
        """Get the next approval level for escalation."""
        if current == ApprovalLevel.L1:
            return ApprovalLevel.L2
        elif current == ApprovalLevel.L2:
            return ApprovalLevel.L3
        else:
            return ApprovalLevel.ADMIN  # L3 escalates to admin
    
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
            "pending": by_status.get("pending", 0),
            "assigned": by_status.get("assigned", 0),
            "completed": by_status.get("completed", 0),
            "total": sum(by_status.values()),
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
        
        # Handle current_level - may not exist in older DBs
        try:
            current_level = ApprovalLevel(row["current_level"]) if row["current_level"] else ApprovalLevel.L1
        except (KeyError, IndexError):
            current_level = ApprovalLevel.L1
        
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
            current_level=current_level,
            assigned_to=row["assigned_to"],
            assigned_at=datetime.fromisoformat(row["assigned_at"]) if row["assigned_at"] else None,
            due_by=datetime.fromisoformat(row["due_by"]) if row["due_by"] else None,
            invoice_data=json.loads(row["invoice_data"]) if row["invoice_data"] else {},
            validation_flags=json.loads(row["validation_flags"]) if row["validation_flags"] else [],
            decision=decision,
        )


def complete_approved_review(
    item: ReviewItem,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Complete payment processing for an approved review item.
    
    This should be called after a reviewer approves an escalated invoice.
    Records the invoice as processed and returns payment details.
    
    Args:
        item: The approved ReviewItem
        db_path: Path to inventory database for recording
        
    Returns:
        Payment result dict with status and reference
    """
    import uuid
    import structlog
    from src.db.queries import record_processed_invoice
    
    log = structlog.get_logger()
    
    if not item.decision or item.decision.action not in (ReviewAction.APPROVE, ReviewAction.CORRECT):
        return {
            "status": "error",
            "error": "Item not approved",
        }
    
    # Generate payment reference
    reference = f"PAY-{uuid.uuid4().hex[:8].upper()}"
    
    log.info("review_approved_payment_processed", 
             run_id=item.run_id, 
             reference=reference,
             reviewer=item.decision.reviewer)
    
    # Update the processed invoice status (it was recorded as pending_review when escalated)
    if db_path:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE processed_invoices SET status = ? WHERE run_id = ?",
            ("paid_after_review", item.run_id)
        )
        updated = cursor.rowcount > 0
        conn.commit()
        conn.close()
        
        if not updated:
            # Record didn't exist, insert it
            record_processed_invoice(
                db_path=db_path,
                invoice_number=item.invoice_number,
                vendor=item.vendor,
                total_amount=item.amount,
                status="paid_after_review",
                run_id=item.run_id,
            )
        
        log.debug("invoice_status_updated", run_id=item.run_id, status="paid_after_review")
        
        # Deduct inventory for approved review items
        if item.invoice_data and "line_items" in item.invoice_data:
            from src.db.queries import deduct_inventory
            
            items_to_deduct = [
                (li.get("item", li.get("name")), li.get("quantity", 0))
                for li in item.invoice_data.get("line_items", [])
                if li.get("item") or li.get("name")
            ]
            
            if items_to_deduct:
                deduct_result = deduct_inventory(items_to_deduct, db_path)
                log.debug("inventory_deducted_after_review", 
                         run_id=item.run_id,
                         deducted=len(deduct_result.get("deducted", [])))
    
    return {
        "status": "success",
        "reference": reference,
        "invoice_number": item.invoice_number,
        "amount": item.amount,
        "approved_by": item.decision.reviewer,
        "timestamp": datetime.utcnow().isoformat(),
    }
