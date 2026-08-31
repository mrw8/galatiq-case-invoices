"""Database query functions for inventory validation."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz, process

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "inventory.db"

# Connection cache for reuse
_connection: sqlite3.Connection | None = None


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Get or create a database connection."""
    global _connection
    if _connection is None:
        _connection = sqlite3.connect(db_path, check_same_thread=False)
        _connection.row_factory = sqlite3.Row
    return _connection


def close_connection() -> None:
    """Close the cached connection."""
    global _connection
    if _connection:
        _connection.close()
        _connection = None


@dataclass
class ItemLookupResult:
    """Result of an item lookup."""

    found: bool
    item_name: str
    stock: int = 0
    unit_price: float = 0.0
    category: str | None = None
    fuzzy_matched: bool = False
    original_query: str = ""
    match_score: float = 100.0


@dataclass
class VendorLookupResult:
    """Result of a vendor lookup."""

    found: bool
    name: str
    status: str = "unknown"
    payment_terms: str | None = None
    is_blacklisted: bool = False


def lookup_item(
    item_name: str,
    db_path: Path | str = DEFAULT_DB_PATH,
    fuzzy_threshold: int = 90,
) -> ItemLookupResult:
    """
    Look up an item in inventory, with optional fuzzy matching.

    Args:
        item_name: Name of item to look up.
        db_path: Path to database.
        fuzzy_threshold: Minimum score (0-100) for fuzzy match.

    Returns:
        ItemLookupResult with item details or not-found status.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Normalize input
    normalized_name = item_name.strip()

    # Try exact match first
    cursor.execute(
        "SELECT item, stock, unit_price, category FROM inventory WHERE item = ?",
        (normalized_name,),
    )
    row = cursor.fetchone()

    if row:
        return ItemLookupResult(
            found=True,
            item_name=row["item"],
            stock=row["stock"],
            unit_price=row["unit_price"],
            category=row["category"],
            fuzzy_matched=False,
            original_query=item_name,
            match_score=100.0,
        )

    # Try case-insensitive match
    cursor.execute(
        "SELECT item, stock, unit_price, category FROM inventory WHERE LOWER(item) = LOWER(?)",
        (normalized_name,),
    )
    row = cursor.fetchone()

    if row:
        return ItemLookupResult(
            found=True,
            item_name=row["item"],
            stock=row["stock"],
            unit_price=row["unit_price"],
            category=row["category"],
            fuzzy_matched=False,
            original_query=item_name,
            match_score=100.0,
        )

    # Try removing spaces (e.g., "Widget A" -> "WidgetA")
    no_spaces = normalized_name.replace(" ", "")
    cursor.execute(
        "SELECT item, stock, unit_price, category FROM inventory WHERE item = ?",
        (no_spaces,),
    )
    row = cursor.fetchone()

    if row:
        return ItemLookupResult(
            found=True,
            item_name=row["item"],
            stock=row["stock"],
            unit_price=row["unit_price"],
            category=row["category"],
            fuzzy_matched=True,  # Mark as fuzzy since we transformed input
            original_query=item_name,
            match_score=95.0,
        )

    # Try fuzzy matching as last resort
    cursor.execute("SELECT item FROM inventory")
    all_items = [row["item"] for row in cursor.fetchall()]

    if all_items:
        best_match = process.extractOne(
            normalized_name,
            all_items,
            scorer=fuzz.ratio,
        )

        if best_match and best_match[1] >= fuzzy_threshold:
            matched_name = best_match[0]
            cursor.execute(
                "SELECT item, stock, unit_price, category FROM inventory WHERE item = ?",
                (matched_name,),
            )
            row = cursor.fetchone()

            if row:
                return ItemLookupResult(
                    found=True,
                    item_name=row["item"],
                    stock=row["stock"],
                    unit_price=row["unit_price"],
                    category=row["category"],
                    fuzzy_matched=True,
                    original_query=item_name,
                    match_score=float(best_match[1]),
                )

    # Not found
    return ItemLookupResult(
        found=False,
        item_name=item_name,
        original_query=item_name,
    )


def check_stock(item_name: str, requested_qty: int, db_path: Path | str = DEFAULT_DB_PATH) -> dict:
    """
    Check if requested quantity is available in stock.

    Returns:
        Dict with keys: available (bool), stock (int), requested (int),
        shortage (int if not available), item_name (str)
    """
    lookup = lookup_item(item_name, db_path)

    if not lookup.found:
        return {
            "available": False,
            "stock": 0,
            "requested": requested_qty,
            "shortage": requested_qty,
            "item_name": item_name,
            "error": "UNKNOWN_ITEM",
        }

    if lookup.stock == 0:
        return {
            "available": False,
            "stock": 0,
            "requested": requested_qty,
            "shortage": requested_qty,
            "item_name": lookup.item_name,
            "error": "ZERO_STOCK",
            "fuzzy_matched": lookup.fuzzy_matched,
        }

    if requested_qty > lookup.stock:
        return {
            "available": False,
            "stock": lookup.stock,
            "requested": requested_qty,
            "shortage": requested_qty - lookup.stock,
            "item_name": lookup.item_name,
            "error": "STOCK_EXCEEDED",
            "fuzzy_matched": lookup.fuzzy_matched,
        }

    return {
        "available": True,
        "stock": lookup.stock,
        "requested": requested_qty,
        "remaining_after": lookup.stock - requested_qty,
        "item_name": lookup.item_name,
        "fuzzy_matched": lookup.fuzzy_matched,
    }


def check_vendor(vendor_name: str, db_path: Path | str = DEFAULT_DB_PATH) -> VendorLookupResult:
    """
    Look up vendor status.

    Returns:
        VendorLookupResult with vendor details or not-found status.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Normalize
    normalized = vendor_name.strip()

    # Try exact match
    cursor.execute(
        "SELECT name, status, payment_terms FROM vendors WHERE name = ?",
        (normalized,),
    )
    row = cursor.fetchone()

    if row:
        return VendorLookupResult(
            found=True,
            name=row["name"],
            status=row["status"],
            payment_terms=row["payment_terms"],
            is_blacklisted=row["status"] == "blacklisted",
        )

    # Try case-insensitive
    cursor.execute(
        "SELECT name, status, payment_terms FROM vendors WHERE LOWER(name) = LOWER(?)",
        (normalized,),
    )
    row = cursor.fetchone()

    if row:
        return VendorLookupResult(
            found=True,
            name=row["name"],
            status=row["status"],
            payment_terms=row["payment_terms"],
            is_blacklisted=row["status"] == "blacklisted",
        )

    # Not found - unknown vendor (might be new, not necessarily bad)
    return VendorLookupResult(
        found=False,
        name=vendor_name,
    )


def record_processed_invoice(
    invoice_number: str,
    status: str,
    total_amount: float,
    vendor: str,
    run_id: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> bool:
    """
    Record a processed invoice for duplicate detection.

    Returns:
        True if recorded successfully, False if duplicate.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO processed_invoices (invoice_number, status, total_amount, vendor, run_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (invoice_number, status, total_amount, vendor, run_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # Duplicate invoice number
        return False


def check_duplicate_invoice(
    invoice_number: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict | None:
    """
    Check if invoice has already been processed.

    Returns:
        Dict with previous processing details if duplicate, None otherwise.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM processed_invoices WHERE invoice_number = ?",
        (invoice_number,),
    )
    row = cursor.fetchone()

    if row:
        return {
            "invoice_number": row["invoice_number"],
            "processed_at": row["processed_at"],
            "status": row["status"],
            "total_amount": row["total_amount"],
            "vendor": row["vendor"],
            "run_id": row["run_id"],
        }
    return None
