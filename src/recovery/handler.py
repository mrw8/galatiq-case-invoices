"""Error recovery and retry handling."""

import json
import sqlite3
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field


class RecoveryStatus(str, Enum):
    """Status of a failed operation."""
    
    PENDING = "pending"          # Waiting for retry
    RETRYING = "retrying"        # Currently being retried
    RECOVERED = "recovered"      # Successfully recovered
    DEAD_LETTER = "dead_letter"  # Permanently failed, needs manual intervention


class RetryPolicy(BaseModel):
    """Configuration for retry behavior."""
    
    max_retries: int = 3
    initial_delay_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 60.0
    
    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number (0-indexed)."""
        delay = self.initial_delay_seconds * (self.backoff_multiplier ** attempt)
        return min(delay, self.max_delay_seconds)


class FailedOperation(BaseModel):
    """A failed operation that may be retried."""
    
    id: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    run_id: str
    invoice_number: str | None = None
    stage: str  # ingestion, validation, approval, payment
    error_type: str
    error_message: str
    
    status: RecoveryStatus = RecoveryStatus.PENDING
    retry_count: int = 0
    next_retry_at: datetime | None = None
    
    # Context needed for retry
    context: dict[str, Any] = Field(default_factory=dict)
    
    # Resolution info
    resolved_at: datetime | None = None
    resolution_notes: str | None = None


class RecoveryHandler:
    """
    Handles error recovery with retries and dead letter queue.
    
    Usage:
        handler = RecoveryHandler()
        
        # Record a failure
        handler.record_failure(
            run_id="run-123",
            stage="payment",
            error=exception,
            context={"invoice_path": "..."}
        )
        
        # Process retries
        handler.process_pending_retries(retry_func)
        
        # Get dead letters for manual review
        dead_letters = handler.get_dead_letters()
    """
    
    def __init__(
        self,
        db_path: str | Path = "recovery.db",
        policy: RetryPolicy | None = None,
    ):
        self.db_path = Path(db_path)
        self.policy = policy or RetryPolicy()
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize recovery database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS failed_operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                run_id TEXT NOT NULL,
                invoice_number TEXT,
                stage TEXT NOT NULL,
                error_type TEXT NOT NULL,
                error_message TEXT NOT NULL,
                status TEXT NOT NULL,
                retry_count INTEGER DEFAULT 0,
                next_retry_at TEXT,
                context TEXT,
                resolved_at TEXT,
                resolution_notes TEXT
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_recovery_status 
            ON failed_operations(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_recovery_next_retry 
            ON failed_operations(next_retry_at)
        """)
        
        conn.commit()
        conn.close()
    
    def record_failure(
        self,
        run_id: str,
        stage: str,
        error: Exception | str,
        invoice_number: str | None = None,
        context: dict | None = None,
    ) -> FailedOperation:
        """Record a failed operation for potential retry."""
        error_type = type(error).__name__ if isinstance(error, Exception) else "Error"
        error_message = str(error)
        
        # Calculate next retry time
        next_retry = datetime.utcnow() + timedelta(
            seconds=self.policy.get_delay(0)
        )
        
        operation = FailedOperation(
            run_id=run_id,
            invoice_number=invoice_number,
            stage=stage,
            error_type=error_type,
            error_message=error_message,
            status=RecoveryStatus.PENDING,
            next_retry_at=next_retry,
            context=context or {},
        )
        
        return self._save(operation)
    
    def _save(self, op: FailedOperation) -> FailedOperation:
        """Save or update a failed operation."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        now = datetime.utcnow()
        
        if op.id is None:
            cursor.execute("""
                INSERT INTO failed_operations
                (created_at, updated_at, run_id, invoice_number, stage, 
                 error_type, error_message, status, retry_count, next_retry_at,
                 context, resolved_at, resolution_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                op.created_at.isoformat(),
                now.isoformat(),
                op.run_id,
                op.invoice_number,
                op.stage,
                op.error_type,
                op.error_message,
                op.status.value,
                op.retry_count,
                op.next_retry_at.isoformat() if op.next_retry_at else None,
                json.dumps(op.context),
                op.resolved_at.isoformat() if op.resolved_at else None,
                op.resolution_notes,
            ))
            op_id = cursor.lastrowid
        else:
            cursor.execute("""
                UPDATE failed_operations SET
                    updated_at = ?,
                    status = ?,
                    retry_count = ?,
                    next_retry_at = ?,
                    resolved_at = ?,
                    resolution_notes = ?
                WHERE id = ?
            """, (
                now.isoformat(),
                op.status.value,
                op.retry_count,
                op.next_retry_at.isoformat() if op.next_retry_at else None,
                op.resolved_at.isoformat() if op.resolved_at else None,
                op.resolution_notes,
                op.id,
            ))
            op_id = op.id
        
        conn.commit()
        conn.close()
        
        return FailedOperation(
            id=op_id,
            created_at=op.created_at,
            updated_at=now,
            run_id=op.run_id,
            invoice_number=op.invoice_number,
            stage=op.stage,
            error_type=op.error_type,
            error_message=op.error_message,
            status=op.status,
            retry_count=op.retry_count,
            next_retry_at=op.next_retry_at,
            context=op.context,
            resolved_at=op.resolved_at,
            resolution_notes=op.resolution_notes,
        )
    
    def get_pending_retries(self) -> list[FailedOperation]:
        """Get operations ready for retry."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        now = datetime.utcnow().isoformat()
        
        cursor.execute("""
            SELECT * FROM failed_operations
            WHERE status = ? AND next_retry_at <= ?
            ORDER BY next_retry_at ASC
        """, (RecoveryStatus.PENDING.value, now))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_operation(row) for row in rows]
    
    def get_dead_letters(self) -> list[FailedOperation]:
        """Get operations that have permanently failed."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM failed_operations
            WHERE status = ?
            ORDER BY created_at DESC
        """, (RecoveryStatus.DEAD_LETTER.value,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_operation(row) for row in rows]
    
    def process_pending_retries(
        self,
        retry_func: Callable[[FailedOperation], bool],
    ) -> dict[str, int]:
        """
        Process all pending retries.
        
        Args:
            retry_func: Function that attempts to recover. 
                       Returns True on success, False on failure.
        
        Returns:
            Dict with counts: {"recovered": n, "retrying": n, "dead_letter": n}
        """
        pending = self.get_pending_retries()
        results = {"recovered": 0, "retrying": 0, "dead_letter": 0}
        
        for op in pending:
            # Mark as retrying
            op = FailedOperation(
                **{**op.model_dump(), "status": RecoveryStatus.RETRYING}
            )
            self._save(op)
            
            try:
                success = retry_func(op)
                
                if success:
                    # Recovered
                    op = FailedOperation(
                        **{
                            **op.model_dump(),
                            "status": RecoveryStatus.RECOVERED,
                            "resolved_at": datetime.utcnow(),
                            "resolution_notes": "Automatic retry succeeded",
                        }
                    )
                    results["recovered"] += 1
                else:
                    raise Exception("Retry returned False")
                    
            except Exception as e:
                # Retry failed
                new_count = op.retry_count + 1
                
                if new_count >= self.policy.max_retries:
                    # Move to dead letter
                    op = FailedOperation(
                        **{
                            **op.model_dump(),
                            "status": RecoveryStatus.DEAD_LETTER,
                            "retry_count": new_count,
                            "resolution_notes": f"Max retries exceeded. Last error: {e}",
                        }
                    )
                    results["dead_letter"] += 1
                else:
                    # Schedule next retry
                    next_delay = self.policy.get_delay(new_count)
                    op = FailedOperation(
                        **{
                            **op.model_dump(),
                            "status": RecoveryStatus.PENDING,
                            "retry_count": new_count,
                            "next_retry_at": datetime.utcnow() + timedelta(seconds=next_delay),
                        }
                    )
                    results["retrying"] += 1
            
            self._save(op)
        
        return results
    
    def mark_resolved(
        self,
        operation_id: int,
        notes: str = "Manually resolved",
    ) -> FailedOperation | None:
        """Manually mark an operation as resolved."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM failed_operations WHERE id = ?", (operation_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        op = self._row_to_operation(row)
        op = FailedOperation(
            **{
                **op.model_dump(),
                "status": RecoveryStatus.RECOVERED,
                "resolved_at": datetime.utcnow(),
                "resolution_notes": notes,
            }
        )
        
        return self._save(op)
    
    def get_stats(self) -> dict[str, int]:
        """Get counts by status."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT status, COUNT(*) FROM failed_operations GROUP BY status
        """)
        
        rows = cursor.fetchall()
        conn.close()
        
        return {row[0]: row[1] for row in rows}
    
    def _row_to_operation(self, row: sqlite3.Row) -> FailedOperation:
        """Convert database row to FailedOperation."""
        return FailedOperation(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            run_id=row["run_id"],
            invoice_number=row["invoice_number"],
            stage=row["stage"],
            error_type=row["error_type"],
            error_message=row["error_message"],
            status=RecoveryStatus(row["status"]),
            retry_count=row["retry_count"],
            next_retry_at=datetime.fromisoformat(row["next_retry_at"]) if row["next_retry_at"] else None,
            context=json.loads(row["context"]) if row["context"] else {},
            resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
            resolution_notes=row["resolution_notes"],
        )
