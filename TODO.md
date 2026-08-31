# TODO / Notes

## Fresh System Testing

**IMPORTANT:** Before submitting, test the full setup on a clean system:

```bash
# Clone fresh
git clone <repo> /tmp/test-invoice-processor
cd /tmp/test-invoice-processor

# Install all dependencies
uv sync --all-extras

# Verify tests pass
uv run pytest tests/ -v

# Verify CLI works
uv run python -m src.main --init-db
uv run python -m src.main --invoice_path data/invoices/invoice_1001.txt

# Verify UI starts
uv run streamlit run src/ui/app.py
```

### Known Setup Issues

1. **`uv sync` vs `uv sync --all-extras`**: Plain `uv sync` does NOT install `[dev]`, `[ui]`, or `[pdf-gen]` extras. Always use `--all-extras` for development.

2. **rapidfuzz import error**: If you see `ModuleNotFoundError: No module named 'rapidfuzz'`, run:
   ```bash
   rm -rf .venv && uv sync --all-extras
   ```

3. **Stale .venv**: If dependencies seem wrong, delete `.venv` and re-sync.

---

## Acknowledged Gaps (Not Implementing)

Per discussion, these are documented but out of scope:

| # | Gap | Description |
|---|-----|-------------|
| 5 | Integration Points | ERP, accounting software, real payment APIs, email ingestion |
| 6 | PO/Three-Way Match | Match invoice to purchase order and receiving report |
| 7 | Multi-Tenant/Auth | User roles, org isolation, approval hierarchies |
| 8 | Currency/Tax | Multi-currency conversion, tax calculation, compliance |

---

## Grok Integration Status

**GrokClient is implemented** (`src/llm/client.py`) but requires:
1. xAI API key with credits (set `XAI_API_KEY` env var)
2. Model name: `grok-2-latest` (may need updating)

To test when credits are available:
```bash
export XAI_API_KEY=your_key
export LLM_BACKEND=grok
uv run python -m src.main --invoice_path data/invoices/invoice_1001.txt
```

**Current status:** API key validated, account needs credits to make calls.

---

## Future Improvements

- [ ] Wire audit/recovery/workflow/feedback into main pipeline
- [ ] Add review queue page to Streamlit UI
- [ ] LangGraph integration when environment supports it
- [ ] Batch processing analytics dashboard
- [ ] LLM token cost tracking per run
- [ ] Test Grok integration with funded API key
