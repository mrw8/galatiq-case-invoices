"""Correction tracking for feedback loop learning."""

import json
import sqlite3
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class CorrectionType(str, Enum):
    """Types of corrections."""
    
    # Extraction corrections
    VENDOR_NAME = "vendor_name"
    INVOICE_NUMBER = "invoice_number"
    DATE = "date"
    DUE_DATE = "due_date"
    AMOUNT = "amount"
    LINE_ITEM = "line_item"
    CURRENCY = "currency"
    
    # Validation corrections  
    ITEM_MAPPING = "item_mapping"      # "Widget A" should map to "WidgetA"
    VENDOR_MAPPING = "vendor_mapping"  # "Acme Inc" should map to "Acme Corp"
    
    # Decision corrections
    FALSE_POSITIVE = "false_positive"  # Was flagged but shouldn't have been
    FALSE_NEGATIVE = "false_negative"  # Wasn't flagged but should have been


class Correction(BaseModel):
    """A correction made by a human reviewer."""
    
    id: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    run_id: str
    invoice_number: str
    correction_type: CorrectionType
    
    field_name: str | None = None  # Specific field if applicable
    original_value: Any
    corrected_value: Any
    
    corrected_by: str
    notes: str | None = None
    
    # Context for learning
    source_format: str | None = None  # pdf, txt, json, etc.
    confidence_was: float | None = None  # What confidence did we have?


class CorrectionStore:
    """
    Store for tracking corrections to enable learning.
    
    Usage:
        store = CorrectionStore()
        
        # Record a correction
        store.record(Correction(
            run_id="run-123",
            invoice_number="INV-1001",
            correction_type=CorrectionType.VENDOR_NAME,
            original_value="Acme Inc",
            corrected_value="Acme Corporation",
            corrected_by="reviewer@example.com",
        ))
        
        # Get common corrections (for learning)
        common = store.get_common_corrections(CorrectionType.ITEM_MAPPING)
        # Returns: [{"original": "Widget A", "corrected": "WidgetA", "count": 15}, ...]
    """
    
    def __init__(self, db_path: str | Path = "corrections.db"):
        self.db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self) -> None:
        """Initialize corrections database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                run_id TEXT NOT NULL,
                invoice_number TEXT NOT NULL,
                correction_type TEXT NOT NULL,
                field_name TEXT,
                original_value TEXT,
                corrected_value TEXT,
                corrected_by TEXT NOT NULL,
                notes TEXT,
                source_format TEXT,
                confidence_was REAL
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_corrections_type 
            ON corrections(correction_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_corrections_invoice 
            ON corrections(invoice_number)
        """)
        
        # Derived mappings table for fast lookup
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS learned_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mapping_type TEXT NOT NULL,
                original_value TEXT NOT NULL,
                mapped_value TEXT NOT NULL,
                occurrence_count INTEGER DEFAULT 1,
                last_seen TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                UNIQUE(mapping_type, original_value)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def record(self, correction: Correction) -> Correction:
        """Record a correction."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Store the correction
        cursor.execute("""
            INSERT INTO corrections
            (created_at, run_id, invoice_number, correction_type, field_name,
             original_value, corrected_value, corrected_by, notes, 
             source_format, confidence_was)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            correction.created_at.isoformat(),
            correction.run_id,
            correction.invoice_number,
            correction.correction_type.value,
            correction.field_name,
            json.dumps(correction.original_value),
            json.dumps(correction.corrected_value),
            correction.corrected_by,
            correction.notes,
            correction.source_format,
            correction.confidence_was,
        ))
        
        correction_id = cursor.lastrowid
        
        # Update learned mappings for mapping-type corrections
        if correction.correction_type in (
            CorrectionType.ITEM_MAPPING,
            CorrectionType.VENDOR_MAPPING,
        ):
            self._update_mapping(
                cursor,
                correction.correction_type.value,
                str(correction.original_value),
                str(correction.corrected_value),
            )
        
        conn.commit()
        conn.close()
        
        return Correction(**{**correction.model_dump(), "id": correction_id})
    
    def _update_mapping(
        self,
        cursor: sqlite3.Cursor,
        mapping_type: str,
        original: str,
        mapped: str,
    ) -> None:
        """Update learned mappings table."""
        now = datetime.utcnow().isoformat()
        
        # Try to update existing
        cursor.execute("""
            UPDATE learned_mappings
            SET occurrence_count = occurrence_count + 1,
                last_seen = ?,
                mapped_value = ?
            WHERE mapping_type = ? AND original_value = ?
        """, (now, mapped, mapping_type, original))
        
        if cursor.rowcount == 0:
            # Insert new
            cursor.execute("""
                INSERT INTO learned_mappings
                (mapping_type, original_value, mapped_value, last_seen)
                VALUES (?, ?, ?, ?)
            """, (mapping_type, original, mapped, now))
    
    def get_mapping(self, mapping_type: CorrectionType, original_value: str) -> str | None:
        """
        Look up a learned mapping.
        
        Returns the mapped value if we've learned this mapping,
        or None if we haven't seen it before.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT mapped_value FROM learned_mappings
            WHERE mapping_type = ? AND original_value = ?
        """, (mapping_type.value, original_value))
        
        row = cursor.fetchone()
        conn.close()
        
        return row[0] if row else None
    
    def get_all_mappings(self, mapping_type: CorrectionType) -> dict[str, str]:
        """Get all learned mappings of a type."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT original_value, mapped_value FROM learned_mappings
            WHERE mapping_type = ?
            ORDER BY occurrence_count DESC
        """, (mapping_type.value,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return {row[0]: row[1] for row in rows}
    
    def get_common_corrections(
        self,
        correction_type: CorrectionType,
        min_occurrences: int = 2,
        limit: int = 50,
    ) -> list[dict]:
        """
        Get commonly made corrections for analysis.
        
        Useful for identifying patterns to improve extraction.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT original_value, corrected_value, COUNT(*) as count
            FROM corrections
            WHERE correction_type = ?
            GROUP BY original_value, corrected_value
            HAVING count >= ?
            ORDER BY count DESC
            LIMIT ?
        """, (correction_type.value, min_occurrences, limit))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "original": json.loads(row[0]),
                "corrected": json.loads(row[1]),
                "count": row[2],
            }
            for row in rows
        ]
    
    def get_correction_rate(self, days: int = 30) -> dict[str, Any]:
        """Get correction statistics for monitoring accuracy."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        since = datetime.utcnow().isoformat()[:10]  # Simplified date calc
        
        # Total corrections by type
        cursor.execute("""
            SELECT correction_type, COUNT(*) 
            FROM corrections
            GROUP BY correction_type
        """)
        by_type = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Total corrections
        cursor.execute("SELECT COUNT(*) FROM corrections")
        total = cursor.fetchone()[0]
        
        # Corrections by source format
        cursor.execute("""
            SELECT source_format, COUNT(*)
            FROM corrections
            WHERE source_format IS NOT NULL
            GROUP BY source_format
        """)
        by_format = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Average confidence of corrected items
        cursor.execute("""
            SELECT AVG(confidence_was)
            FROM corrections
            WHERE confidence_was IS NOT NULL
        """)
        avg_confidence = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "total_corrections": total,
            "by_type": by_type,
            "by_source_format": by_format,
            "avg_confidence_when_wrong": round(avg_confidence, 3) if avg_confidence else None,
        }
    
    def get_for_invoice(self, invoice_number: str) -> list[Correction]:
        """Get all corrections for an invoice."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM corrections
            WHERE invoice_number = ?
            ORDER BY created_at ASC
        """, (invoice_number,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [self._row_to_correction(row) for row in rows]
    
    def _row_to_correction(self, row: sqlite3.Row) -> Correction:
        """Convert database row to Correction."""
        return Correction(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            run_id=row["run_id"],
            invoice_number=row["invoice_number"],
            correction_type=CorrectionType(row["correction_type"]),
            field_name=row["field_name"],
            original_value=json.loads(row["original_value"]) if row["original_value"] else None,
            corrected_value=json.loads(row["corrected_value"]) if row["corrected_value"] else None,
            corrected_by=row["corrected_by"],
            notes=row["notes"],
            source_format=row["source_format"],
            confidence_was=row["confidence_was"],
        )
