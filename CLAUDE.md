# CLAUDE.md - Project Conventions

## Overview
Invoice processing automation system with multi-agent pipeline.
Framework: LangGraph. LLM: xAI Grok (abstracted behind interface).

## Phase 1 Decisions

### LLM Client Strategy
- **Interface-first**: `LLMClient` protocol/ABC that all implementations follow
- **MockClient**: Default implementation, returns deterministic responses
- **GrokClient**: Real implementation, added later (uses OpenAI SDK with xAI base URL)
- **Selection**: Environment variable `LLM_BACKEND=mock|grok` (default: mock)

### Mock Behavior
The mock client should return predictable, parseable responses for each agent:
- **Ingestion**: Returns a valid `Invoice` Pydantic model as JSON
- **Validation**: Returns structured validation flags
- **Approval**: Returns decision with reasoning
- Responses keyed off input hashes or patterns for determinism in tests

### Directory Structure
```
src/
  __init__.py
  main.py                 # CLI entrypoint
  models/
    __init__.py
    invoice.py            # Invoice, LineItem, ValidationResult, etc.
    pipeline.py           # PipelineState
  llm/
    __init__.py
    client.py             # LLMClient protocol + MockClient + GrokClient stub
  db/
    __init__.py
    seed.py               # Creates inventory.db
    queries.py            # lookup_item, check_stock, check_vendor
  agents/
    __init__.py
    ingestion.py          # PDF/TXT/CSV/JSON/XML -> Invoice
    validation.py         # Inventory checks, flag generation
    approval.py           # Rules + generator/critic loop
    payment.py            # mock_payment + rejection logging
  graph/
    __init__.py
    pipeline.py           # LangGraph orchestration
  utils/
    __init__.py
    logging.py            # Structured JSON logging with run_id
    parsing.py            # Date parsing, fuzzy matching, etc.
tests/
  __init__.py
  conftest.py             # Fixtures, MockClient setup
  test_ingestion.py
  test_validation.py
  test_approval.py
  test_payment.py
  test_pipeline.py        # End-to-end scenarios
runs/                     # JSON trace output per run
```

## Code Conventions

### Pydantic Models
- Use Pydantic v2 syntax (`model_validator`, `field_validator`)
- All models immutable by default (`frozen=True` for value objects)
- `PipelineState` is mutable (LangGraph state)

### Error Handling
- Never swallow exceptions silently
- Use typed custom exceptions: `IngestionError`, `ValidationError`, etc.
- All errors logged with run_id context

### Logging
- Structured JSON logs to stdout
- Every agent emits one event per invocation
- Fields: `run_id`, `agent`, `timestamp`, `event_type`, `data`

### Testing
- Pytest with `MockClient` - no network calls ever
- Tests must complete in <10 seconds total
- One test per scenario from README table
- Fixtures provide pre-parsed invoices

### Type Hints
- Full type hints everywhere
- Use `typing.Protocol` for interfaces
- Run `mypy` in strict mode

## LLM Prompt Patterns

### Structured Output
```python
response = client.chat(
    messages=[...],
    response_format={"type": "json_object"},  # Or JSON schema
)
# Parse with Pydantic, retry on failure (max 2)
```

### Self-Correction Loop
```python
for attempt in range(max_retries + 1):
    response = client.extract(text)
    try:
        return Invoice.model_validate_json(response)
    except ValidationError as e:
        if attempt == max_retries:
            raise
        # Re-prompt with error context
        messages.append({"role": "user", "content": f"Fix this: {e}"})
```

## Key Validation Flags
```python
class ValidationFlag(str, Enum):
    UNKNOWN_ITEM = "UNKNOWN_ITEM"
    STOCK_EXCEEDED = "STOCK_EXCEEDED"
    ZERO_STOCK = "ZERO_STOCK"
    NEGATIVE_QTY = "NEGATIVE_QTY"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    MISSING_VENDOR = "MISSING_VENDOR"
    MISSING_DUE_DATE = "MISSING_DUE_DATE"
    INVALID_DATE = "INVALID_DATE"
    FUZZY_MATCH = "FUZZY_MATCH"
    FOREIGN_CURRENCY = "FOREIGN_CURRENCY"
    HIGH_VALUE = "HIGH_VALUE"
    FRAUD_SUSPECT = "FRAUD_SUSPECT"
    BLACKLISTED_VENDOR = "BLACKLISTED_VENDOR"
```

## Approval Rules (in order)
1. Any hard flag (UNKNOWN_ITEM, STOCK_EXCEEDED, ZERO_STOCK, NEGATIVE_QTY, FRAUD_SUSPECT, BLACKLISTED_VENDOR) -> REJECT
2. Total >= $10,000 -> requires extra scrutiny
3. FUZZY_MATCH present -> CONDITIONAL (needs confirmation)
4. FOREIGN_CURRENCY -> NEEDS_HUMAN
5. DUPLICATE_INVOICE -> NEEDS_HUMAN
6. Otherwise -> generator/critic loop for APPROVE/REJECT

## Commands

```bash
# Run single invoice
uv run python -m src.main --invoice_path data/invoices/invoice_1001.txt

# Run batch (directory)
uv run python -m src.main --invoice_path data/invoices/

# Use real LLM (Grok)
LLM_BACKEND=grok XAI_API_KEY=your_key uv run python -m src.main --invoice_path ...

# Run tests
uv run pytest tests/ -v

# Type check
uv run mypy src/ --strict
```

## What We're NOT Doing (Scope Cuts)
- No real xAI integration in Phase 1 (mock only)
- No UI until Phase 5
- No cloud deployment
- No real payment API
- No email ingestion (just files)
