# Phase 0 Recon Notes - Invoice Inventory

## File Format Summary

| Format | Count | Files |
|--------|-------|-------|
| TXT    | 7     | 1001, 1002, 1003, 1008, 1010, 1011, 1012 |
| JSON   | 6     | 1004, 1004_revised, 1005, 1009, 1013, 1016 |
| CSV    | 3     | 1006, 1007, 1015 |
| PDF    | 3     | 1011, 1012, 1013 (generated via `generate_pdfs.py`) |
| XML    | 1     | 1014 |

---

## Invoice-by-Invoice Analysis

###  INV-1001 (TXT) - CLEAN BASELINE
- **Vendor:** Widgets Inc.
- **Items:** WidgetA (10), WidgetB (5)
- **Total:** $5,000
- **Status:** All within stock, well-formatted
- **Expected Outcome:** APPROVED

---

###  INV-1002 (TXT) - TYPOS + STOCK EXCEEDED
- **Vendor:** Gadgets Co.
- **Items:** GadgetX (20) — **EXCEEDS STOCK (only 5)**
- **Total:** $15,000 — **OVER $10K THRESHOLD**
- **Typos/Issues:**
  - "INVOCE" instead of "INVOICE"
  - "Vndr:" instead of "Vendor:"
  - "Itms:" instead of "Items:"
  - Abbreviated fields: "Dt", "Due Dt", "Pymnt"
  - Non-standard qty format: `qty 20 @ $750 ea`
  - Invoice number missing "INV-" prefix: just `1002`
- **Flags:** `STOCK_EXCEEDED`, `HIGH_VALUE_SCRUTINY`
- **Expected Outcome:** REJECTED

---

###  INV-1003 (TXT) - FRAUD INDICATORS + ZERO STOCK
- **Vendor:** Fraudster LLC 
- **Items:** FakeItem (100) — **ZERO STOCK**
- **Total:** $100,000 — **EXTREMELY HIGH**
- **Red Flags:**
  - Due date: "yesterday" (invalid/urgent pressure)
  - "URGENT - Pay immediately to avoid penalties!!!"
  - "Wire transfer preferred" (classic fraud indicator)
  - Vendor name literally contains "Fraudster"
- **Flags:** `ZERO_STOCK`, `FRAUD_SUSPECT`, `INVALID_DATE`, `HIGH_VALUE_SCRUTINY`
- **Expected Outcome:** REJECTED (hard reject)

---

###  INV-1004 (JSON) - CLEAN STRUCTURED
- **Vendor:** Precision Parts Ltd.
- **Items:** WidgetA (3), WidgetB (2)
- **Total:** $1,890 (incl. 8% tax)
- **Status:** Well-structured JSON, within stock
- **Expected Outcome:** APPROVED

---

###  INV-1004_revised (JSON) - DUPLICATE INVOICE NUMBER
- **Vendor:** Precision Parts Ltd.
- **Items:** WidgetA (3), WidgetB (2), GadgetX (5)
- **Total:** $5,940
- **Issue:** Same invoice number `INV-1004` with revision marker `R1`
- **Flags:** `DUPLICATE_INVOICE` (same ID, different content)
- **Expected Outcome:** NEEDS_HUMAN or conditional approval
- **Note:** System must detect duplicates and handle revisions

---

###  INV-1005 (JSON) - MULTIPLE STOCK ISSUES + HIGH VALUE
- **Vendor:** Global Supply Chain Partners
- **Items:**
  - WidgetA (14) — stock is 15, OK but close
  - GadgetX (8) — **EXCEEDS STOCK (only 5)**
  - WidgetB (10) — **MATCHES STOCK EXACTLY**
- **Total:** $15,225 — **OVER $10K THRESHOLD**
- **Flags:** `STOCK_EXCEEDED`, `HIGH_VALUE_SCRUTINY`
- **Expected Outcome:** REJECTED

---

###  INV-1006 (CSV) - CLEAN KEY-VALUE FORMAT
- **Vendor:** Acme Industrial Supplies
- **Items:** WidgetA (5), WidgetB (3)
- **Total:** $2,750
- **Format Note:** Non-standard CSV (field,value pairs, not columnar)
- **Expected Outcome:** APPROVED

---

###  INV-1007 (CSV) - MULTIPLE STOCK EXCEEDED + HIGH VALUE
- **Vendor:** MegaWidgets Corp
- **Items:**
  - WidgetA (20) — **EXCEEDS STOCK (only 15)**
  - WidgetB (15) — **EXCEEDS STOCK (only 10)**
  - GadgetX (3) — within stock
- **Total:** $15,525 — **OVER $10K THRESHOLD**
- **Format Note:** Columnar CSV with summary rows
- **Flags:** `STOCK_EXCEEDED` (x2), `HIGH_VALUE_SCRUTINY`
- **Expected Outcome:** REJECTED

---

###  INV-1008 (TXT) - UNKNOWN ITEMS (EMAIL FORMAT)
- **Vendor:** NoProd Industries
- **Items:**
  - SuperGizmo (12) — **NOT IN DATABASE**
  - MegaSprocket (6) — **NOT IN DATABASE**
- **Total:** $9,900
- **Format Note:** Embedded in email body (From:/To:/Subject:)
- **Flags:** `UNKNOWN_ITEM` (x2)
- **Expected Outcome:** REJECTED or NEEDS_HUMAN

---

###  INV-1009 (JSON) - NEGATIVE QUANTITY + MISSING FIELDS
- **Vendor:** "" (EMPTY STRING) 
- **Items:**
  - WidgetA (-5) — **NEGATIVE QUANTITY**
  - WidgetB (2) — OK
- **Total:** -$250 — **NEGATIVE TOTAL**
- **Missing Fields:**
  - Vendor name: empty string
  - Vendor address: `null`
  - Due date: `null`
  - Payment terms: empty string
- **Flags:** `NEGATIVE_QTY`, `MISSING_VENDOR`, `MISSING_DUE_DATE`, `INVALID_TOTAL`
- **Expected Outcome:** REJECTED (data integrity failure)

---

###  INV-1010 (TXT) - DUPLICATE LINE ITEMS + MIXED PRICING
- **Vendor:** Consolidated Materials Group
- **Items:**
  - WidgetA (8) @ $250 = $2,000
  - WidgetB (4) @ $500 = $2,000
  - GadgetX (2) @ $750 = $1,500
  - WidgetA (4) @ $300 (rush order) = $1,200 **← SAME ITEM, DIFFERENT PRICE**
- **Additional Charges:** Shipping $150
- **Total:** $7,185
- **Notes:** WidgetA appears twice with different unit prices. System should aggregate: WidgetA total = 12 (within stock of 15)
- **Flags:** `DUPLICATE_LINE_ITEM` (warning), `HAS_SHIPPING`
- **Expected Outcome:** APPROVED (with aggregation logic)

---

###  INV-1011 (PDF + TXT) - CLEAN BASELINE
- **Vendor:** Summit Manufacturing Co.
- **Items:** WidgetA (6), WidgetB (3)
- **Total:** $3,000
- **Format Note:** Both PDF and TXT versions exist
- **Expected Outcome:** APPROVED

---

###  INV-1012 (PDF + TXT) - OCR-STYLE TYPOS
- **Vendor:** QuickShip Distributers (note: "Distributers" typo)
- **Items:**
  - "Widget A" (12) — **SPACE IN NAME (fuzzy match → WidgetA)**
  - "WidgetB" (7) — OK
  - "Gadget X" (4) — **SPACE IN NAME (fuzzy match → GadgetX)**
- **Total:** $9,975
- **OCR Artifacts:**
  - `26-Jan-2O26` — letter O instead of zero in year
  - `$3,500.O0` — letter O instead of zero in amount
  - `INV 1012` — missing hyphen
  - `Accounts Payble` — typo
- **Flags:** `FUZZY_MATCH` (x2), `OCR_ARTIFACTS`
- **Expected Outcome:** APPROVED (with fuzzy matching)

---

###  INV-1013 (JSON + PDF) - BULK ORDER + MULTIPLE STOCK EXCEEDED + HIGH VALUE
- **Vendor:** Atlas Industrial Supply
- **Items (8 line items, same items repeated):**
  - WidgetA: 15 + 5 + 2 = **22 total** — **EXCEEDS STOCK (only 15)**
  - WidgetB: 10 + 8 = **18 total** — **EXCEEDS STOCK (only 10)**
  - GadgetX: 5 + 3 + 1 = **9 total** — **EXCEEDS STOCK (only 5)**
- **Total:** $22,562.80 — **OVER $10K THRESHOLD**
- **Notes:** Volume discounts, expedited items, replacements, samples
- **Flags:** `STOCK_EXCEEDED` (x3), `HIGH_VALUE_SCRUTINY`, `BULK_ORDER`
- **Expected Outcome:** REJECTED

---

###  INV-1014 (XML) - FOREIGN CURRENCY
- **Vendor:** TechParts International
- **Items:** WidgetA (4), WidgetB (6)
- **Total:** €4,125 — **CURRENCY: EUR (not USD)**
- **Format Note:** Only XML file in the dataset
- **Flags:** `FOREIGN_CURRENCY`
- **Expected Outcome:** NEEDS_HUMAN (currency conversion required)

---

###  INV-1015 (CSV) - CLEAN COLUMNAR
- **Vendor:** Reliable Components Inc.
- **Items:** WidgetA (10), WidgetB (5), GadgetX (2)
- **Total:** $6,500
- **Format Note:** Standard columnar CSV
- **Expected Outcome:** APPROVED

---

###  INV-1016 (JSON) - UNKNOWN ITEM
- **Vendor:** Widgets Inc.
- **Items:**
  - WidgetA (4) — OK
  - WidgetB (2) — OK
  - WidgetC (3) — **NOT IN DATABASE**
- **Total:** $3,233
- **Flags:** `UNKNOWN_ITEM`
- **Expected Outcome:** REJECTED or NEEDS_HUMAN

---

## Edge Case Summary

| Category | Invoices | Count |
|----------|----------|-------|
| **Clean/Baseline** | 1001, 1004, 1006, 1011, 1015 | 5 |
| **Stock Exceeded** | 1002, 1005, 1007, 1013 | 4 |
| **Zero Stock (Fraud)** | 1003 | 1 |
| **Unknown Items** | 1008, 1016 | 2 |
| **Negative Quantity** | 1009 | 1 |
| **Missing Fields** | 1009 | 1 |
| **Duplicate Invoice #** | 1004_revised | 1 |
| **Duplicate Line Items** | 1010, 1013 | 2 |
| **Fuzzy Match Needed** | 1012 | 1 |
| **Typos/OCR Artifacts** | 1002, 1012 | 2 |
| **Foreign Currency** | 1014 | 1 |
| **High Value (>$10K)** | 1002, 1003, 1005, 1007, 1013 | 5 |
| **Fraud Indicators** | 1003 | 1 |
| **Email Format** | 1008 | 1 |

---

## Database Schema Requirements

### Minimum Seed Data (from README)
```sql
CREATE TABLE inventory (item TEXT PRIMARY KEY, stock INTEGER);
INSERT INTO inventory VALUES
  ('WidgetA', 15),
  ('WidgetB', 10),
  ('GadgetX', 5),
  ('FakeItem', 0);
```

### Recommended Extensions

```sql
-- Enhanced inventory with pricing for amount validation
CREATE TABLE inventory (
  item TEXT PRIMARY KEY,
  stock INTEGER NOT NULL,
  unit_price DECIMAL(10,2) NOT NULL,
  category TEXT
);

INSERT INTO inventory VALUES
  ('WidgetA', 15, 250.00, 'widget'),
  ('WidgetB', 10, 500.00, 'widget'),
  ('GadgetX', 5, 750.00, 'gadget'),
  ('FakeItem', 0, 1000.00, 'fraudulent');

-- Vendor table for vendor validation
CREATE TABLE vendors (
  name TEXT PRIMARY KEY,
  status TEXT DEFAULT 'active',  -- active, suspended, blacklisted
  payment_terms TEXT DEFAULT 'Net 30'
);

INSERT INTO vendors VALUES
  ('Widgets Inc.', 'active', 'Net 15'),
  ('Gadgets Co.', 'active', 'Net 30'),
  ('Precision Parts Ltd.', 'active', 'Net 30'),
  ('Global Supply Chain Partners', 'active', 'Net 60'),
  ('Acme Industrial Supplies', 'active', 'Net 15'),
  ('MegaWidgets Corp', 'active', 'Net 30'),
  ('NoProd Industries', 'active', 'Net 30'),
  ('Consolidated Materials Group', 'active', 'Net 30'),
  ('Summit Manufacturing Co.', 'active', 'Net 30'),
  ('QuickShip Distributers', 'active', 'Net 30'),
  ('Atlas Industrial Supply', 'active', 'Net 60'),
  ('TechParts International', 'active', 'Net 30'),
  ('Reliable Components Inc.', 'active', 'Net 30'),
  ('Fraudster LLC', 'blacklisted', 'Immediate');  -- 

-- Processed invoices for duplicate detection
CREATE TABLE processed_invoices (
  invoice_number TEXT PRIMARY KEY,
  processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  status TEXT,  -- approved, rejected, escalated
  total_amount DECIMAL(10,2),
  vendor TEXT
);
```

---

## Validation Flags to Implement

| Flag | Description | Severity |
|------|-------------|----------|
| `UNKNOWN_ITEM` | Item not in inventory database | Hard reject |
| `STOCK_EXCEEDED` | Quantity > available stock | Hard reject |
| `ZERO_STOCK` | Item exists but stock = 0 | Hard reject |
| `NEGATIVE_QTY` | Line item has negative quantity | Hard reject |
| `AMOUNT_MISMATCH` | Line items don't sum to total | Needs review |
| `DUPLICATE_INVOICE` | Invoice number already processed | Needs review |
| `MISSING_VENDOR` | Vendor field empty/null | Hard reject |
| `MISSING_DUE_DATE` | Due date empty/null | Warning |
| `INVALID_DATE` | Unparseable date (e.g., "yesterday") | Warning |
| `FUZZY_MATCH` | Item matched with fuzzy logic | Conditional |
| `FOREIGN_CURRENCY` | Non-USD currency | Needs review |
| `HIGH_VALUE` | Total ≥ $10,000 | Extra scrutiny |
| `FRAUD_SUSPECT` | Multiple fraud indicators | Hard reject |
| `BLACKLISTED_VENDOR` | Vendor on blacklist | Hard reject |

---

## Test Scenario Matrix

| Test | Invoice | Expected Flags | Expected Outcome |
|------|---------|----------------|------------------|
| Normal within stock | 1001 | none | APPROVED |
| Stock exceeded | 1002 | STOCK_EXCEEDED, HIGH_VALUE | REJECTED |
| Zero stock fraud | 1003 | ZERO_STOCK, FRAUD_SUSPECT, HIGH_VALUE | REJECTED |
| Clean JSON | 1004 | none | APPROVED |
| Unknown items | 1008 | UNKNOWN_ITEM (x2) | REJECTED |
| Negative quantity | 1009 | NEGATIVE_QTY, MISSING_VENDOR | REJECTED |
| Fuzzy match | 1012 | FUZZY_MATCH (conditional) | APPROVED |
| Unknown item | 1016 | UNKNOWN_ITEM | REJECTED |
| Foreign currency | 1014 | FOREIGN_CURRENCY | NEEDS_HUMAN |
| Duplicate invoice | 1004_revised | DUPLICATE_INVOICE | NEEDS_HUMAN |

---

## Format Parsing Notes

### TXT Formats Observed
1. **Clean structured** (1001, 1003, 1011): Key-value pairs, clear sections
2. **Abbreviated/typo-ridden** (1002): Shortened field names, non-standard formats
3. **Email embedded** (1008): Full email headers with invoice in body
4. **OCR-like** (1012): Monospace, typos, letter/digit confusion
5. **Table formatted** (1010): ASCII art tables with alignment

### CSV Formats Observed
1. **Key-value pairs** (1006): `field,value` format (non-standard)
2. **Columnar with repetition** (1007, 1015): Standard headers, data rows, summary rows

### JSON Structure
All JSON files follow consistent schema:
- `invoice_number`, `vendor` (object with name/address), `date`, `due_date`
- `line_items` array with `item`, `quantity`, `unit_price`
- `subtotal`, `tax_rate`, `tax_amount`, `total`, `currency`

### XML (1014)
Standard nested structure. Only foreign currency invoice.

---

## Key Implementation Decisions

1. **Fuzzy matching**: Use Levenshtein distance or similar for item names (handle "Widget A" → "WidgetA")
2. **Date parsing**: Must handle multiple formats: ISO, `Jan 30 2026`, `26-Jan-2O26`, "yesterday"
3. **Currency normalization**: Default to USD, flag non-USD for human review
4. **Duplicate aggregation**: Sum quantities for same item across line items before stock check
5. **Invoice number normalization**: Strip whitespace, add missing prefixes (1002 → INV-1002)
6. **Amount validation**: Verify line_total = qty × unit_price, subtotal = Σ line_totals
7. **Idempotency**: Track processed invoices to prevent duplicate payments
