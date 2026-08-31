"""Ingestion agent: extracts structured data from invoice documents."""

from pathlib import Path

from src.llm.client import LLMClient
from src.models.invoice import Invoice
from src.models.pipeline import PipelineState
from src.utils.logging import AgentLogger


class IngestionError(Exception):
    """Raised when invoice ingestion fails."""

    pass


class IngestionAgent:
    """
    Agent responsible for extracting structured invoice data from raw documents.

    Supports PDF, TXT, CSV, JSON, and XML formats.
    Uses LLM for unstructured text extraction with self-correction loop.
    """

    MAX_RETRIES = 2

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def run(self, state: PipelineState) -> PipelineState:
        """
        Execute ingestion stage.

        Args:
            state: Current pipeline state with source_path set.

        Returns:
            Updated state with invoice populated (or error set).
        """
        logger = AgentLogger("ingestion", state.run_id, state)
        logger.started({"source": state.source_path})

        try:
            # Load raw content
            raw_content = self._load_file(state.source_path)
            state.raw_content = raw_content

            # Extract invoice
            invoice = self._extract_invoice(raw_content, state.source_path, logger)
            state.invoice = invoice
            state.ingestion_attempts = logger._retry_count if hasattr(logger, "_retry_count") else 1

            logger.completed({
                "invoice_number": invoice.invoice_number,
                "vendor": invoice.vendor.name,
                "total": str(invoice.total),
                "line_items": len(invoice.line_items),
            })

        except Exception as e:
            state.error = str(e)
            state.error_stage = "ingestion"
            logger.error(str(e))

        return state

    def _load_file(self, path: str) -> str:
        """Load file content based on format."""
        file_path = Path(path)

        if not file_path.exists():
            raise IngestionError(f"File not found: {path}")

        suffix = file_path.suffix.lower()

        if suffix == ".pdf":
            return self._load_pdf(file_path)
        elif suffix in (".txt", ".csv", ".json", ".xml"):
            return file_path.read_text(encoding="utf-8")
        else:
            raise IngestionError(f"Unsupported file format: {suffix}")

    def _load_pdf(self, path: Path) -> str:
        """Extract text from PDF using pdfplumber."""
        try:
            import pdfplumber
        except ImportError as e:
            raise IngestionError("pdfplumber required for PDF: pip install pdfplumber") from e

        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)

        if not text_parts:
            raise IngestionError(f"No text extracted from PDF: {path}")

        return "\n".join(text_parts)

    def _extract_invoice(
        self,
        raw_content: str,
        source_path: str,
        logger: AgentLogger,
    ) -> Invoice:
        """
        Extract structured invoice from raw content using LLM.

        Implements self-correction loop on parse failures.
        """
        file_ext = Path(source_path).suffix.lower()

        # For JSON files, try direct parsing first
        if file_ext == ".json":
            invoice = self._try_direct_json_parse(raw_content, source_path)
            if invoice:
                return invoice

        # Use LLM for extraction with persona
        from src.prompts.personas import INGESTION_SYSTEM_PROMPT
        system_prompt = INGESTION_SYSTEM_PROMPT

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Extract invoice data from this document:\n\n{raw_content}"},
        ]

        # Try extraction with self-correction loop
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.llm.chat_json(messages)
                invoice = self._parse_invoice_response(response, source_path)
                return invoice

            except Exception as e:
                if attempt < self.MAX_RETRIES:
                    logger.retry(attempt + 1, str(e))
                    # Add error feedback for self-correction
                    messages.append({
                        "role": "assistant",
                        "content": str(response) if "response" in dir() else "{}",
                    })
                    messages.append({
                        "role": "user",
                        "content": f"That response failed validation: {e}\nPlease fix and try again.",
                    })
                else:
                    raise IngestionError(
                        f"Failed to extract invoice after {self.MAX_RETRIES + 1} attempts: {e}"
                    ) from e

        # Should never reach here
        raise IngestionError("Extraction failed unexpectedly")

    def _try_direct_json_parse(self, content: str, source_path: str) -> Invoice | None:
        """Try to parse JSON content directly without LLM."""
        import json
        from decimal import Decimal

        try:
            data = json.loads(content)

            # Map to our Invoice model
            vendor_data = data.get("vendor", {})
            if isinstance(vendor_data, str):
                vendor_data = {"name": vendor_data}

            line_items = []
            for item in data.get("line_items", []):
                line_items.append({
                    "item": item.get("item", ""),
                    "quantity": int(item.get("quantity", 0)),
                    "unit_price": Decimal(str(item.get("unit_price", 0))),
                    "amount": Decimal(str(item["amount"])) if item.get("amount") else None,
                    "note": item.get("note"),
                })

            return Invoice(
                invoice_number=data.get("invoice_number", ""),
                vendor={"name": vendor_data.get("name", ""), "address": vendor_data.get("address")},
                date=data.get("date"),
                due_date=data.get("due_date"),
                line_items=line_items,
                subtotal=Decimal(str(data["subtotal"])) if data.get("subtotal") else None,
                tax_rate=Decimal(str(data["tax_rate"])) if data.get("tax_rate") else None,
                tax_amount=Decimal(str(data["tax_amount"])) if data.get("tax_amount") else None,
                total=Decimal(str(data.get("total", 0))),
                currency=data.get("currency", "USD"),
                payment_terms=data.get("payment_terms"),
                notes=data.get("notes"),
                source_file=source_path,
            )
        except Exception:
            return None

    def _parse_invoice_response(self, response: dict, source_path: str) -> Invoice:
        """Parse LLM response into Invoice model."""
        from decimal import Decimal

        vendor_data = response.get("vendor", {})
        if isinstance(vendor_data, str):
            vendor_data = {"name": vendor_data}

        line_items = []
        for item in response.get("line_items", []):
            line_items.append({
                "item": item.get("item", ""),
                "quantity": int(item.get("quantity", 0)),
                "unit_price": Decimal(str(item.get("unit_price", 0))),
                "amount": Decimal(str(item["amount"])) if item.get("amount") else None,
                "note": item.get("note"),
            })

        return Invoice(
            invoice_number=response.get("invoice_number", ""),
            vendor={"name": vendor_data.get("name", ""), "address": vendor_data.get("address")},
            date=response.get("date"),
            due_date=response.get("due_date"),
            line_items=line_items,
            subtotal=Decimal(str(response["subtotal"])) if response.get("subtotal") else None,
            tax_rate=Decimal(str(response["tax_rate"])) if response.get("tax_rate") else None,
            tax_amount=Decimal(str(response["tax_amount"])) if response.get("tax_amount") else None,
            total=Decimal(str(response.get("total", 0))),
            currency=response.get("currency", "USD"),
            payment_terms=response.get("payment_terms"),
            notes=response.get("notes"),
            source_file=source_path,
        )
