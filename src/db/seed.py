"""Database seeding script for inventory and vendor data."""

import sqlite3
from pathlib import Path

import structlog

DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "inventory.db"

log = structlog.get_logger()


def seed_database(db_path: Path | str = DEFAULT_DB_PATH, reset: bool = False) -> None:
    """
    Create and seed the inventory database.

    Args:
        db_path: Path to SQLite database file.
        reset: If True, drop existing tables first.
    """
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        if reset:
            cursor.execute("DROP TABLE IF EXISTS inventory")
            cursor.execute("DROP TABLE IF EXISTS vendors")
            cursor.execute("DROP TABLE IF EXISTS processed_invoices")

        # Create inventory table with extended schema
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                item TEXT PRIMARY KEY,
                stock INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                category TEXT,
                min_order_qty INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create vendors table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vendors (
                name TEXT PRIMARY KEY,
                status TEXT DEFAULT 'active',
                payment_terms TEXT DEFAULT 'Net 30',
                address TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create processed invoices table for duplicate detection
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_number TEXT NOT NULL,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT,
                total_amount REAL,
                vendor TEXT,
                run_id TEXT UNIQUE
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_invoice_number 
            ON processed_invoices(invoice_number)
        """)

        # Seed inventory data
        inventory_data = [
            ("WidgetA", 500, 250.00, "widget"),
            ("WidgetB", 300, 500.00, "widget"),
            ("GadgetX", 150, 750.00, "gadget"),
            ("FakeItem", 0, 1000.00, "fraudulent"),  # Keep at 0 for testing zero-stock scenarios
        ]

        cursor.executemany(
            "INSERT OR REPLACE INTO inventory (item, stock, unit_price, category) VALUES (?, ?, ?, ?)",
            inventory_data,
        )

        # Seed vendors data (from NOTES.md analysis)
        vendors_data = [
            ("Widgets Inc.", "active", "Net 15", "123 Widget Way"),
            ("Gadgets Co.", "active", "Net 30", "456 Gadget Blvd"),
            ("Precision Parts Ltd.", "active", "Net 30", "742 Evergreen Terrace, Springfield, IL"),
            ("Global Supply Chain Partners", "active", "Net 60", "1600 Pennsylvania Ave"),
            ("Acme Industrial Supplies", "active", "Net 15", None),
            ("MegaWidgets Corp", "active", "Net 30", None),
            ("NoProd Industries", "active", "Net 30", None),
            ("Consolidated Materials Group", "active", "Net 30", None),
            ("Summit Manufacturing Co.", "active", "Net 30", None),
            ("QuickShip Distributers", "active", "Net 30", None),
            ("Atlas Industrial Supply", "active", "Net 60", "500 Commerce Blvd, Detroit, MI"),
            ("TechParts International", "active", "Net 30", None),
            ("Reliable Components Inc.", "active", "Net 30", None),
            # Blacklisted vendor for fraud testing
            ("Fraudster LLC", "blacklisted", "Immediate", None),
        ]

        cursor.executemany(
            "INSERT OR REPLACE INTO vendors (name, status, payment_terms, address) VALUES (?, ?, ?, ?)",
            vendors_data,
        )

        conn.commit()
        log.info("database_seeded", item_count=len(inventory_data), vendor_count=len(vendors_data))
        print(f"Database seeded successfully at {db_path}")
        print(f"  - {len(inventory_data)} inventory items")
        print(f"  - {len(vendors_data)} vendors")

    finally:
        conn.close()


def get_inventory_stats(db_path: Path | str = DEFAULT_DB_PATH) -> dict:
    """Get summary stats about the inventory database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT COUNT(*) FROM inventory")
        item_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM vendors")
        vendor_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM vendors WHERE status = 'blacklisted'")
        blacklisted_count = cursor.fetchone()[0]

        cursor.execute("SELECT SUM(stock) FROM inventory")
        total_stock = cursor.fetchone()[0] or 0

        return {
            "item_count": item_count,
            "vendor_count": vendor_count,
            "blacklisted_vendors": blacklisted_count,
            "total_stock_units": total_stock,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    reset = "--reset" in sys.argv
    seed_database(reset=reset)

    stats = get_inventory_stats()
    print(f"\nDatabase stats: {stats}")
