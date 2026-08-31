"""Validation agent: verifies invoice against inventory database."""

from src.db.queries import check_duplicate_invoice, check_stock, check_vendor, lookup_item
from src.models.invoice import (
    FlagDetail,
    Invoice,
    ValidationFlag,
    ValidationResult,
)
from src.models.pipeline import PipelineState
from src.policies import get_policies
from src.utils.logging import AgentLogger


class ValidationAgent:
    """
    Agent responsible for validating invoice data against inventory.

    Checks:
    - Item existence (with fuzzy matching)
    - Stock availability
    - Vendor status
    - Data integrity (negative quantities, missing fields)
    - Duplicate invoices
    - Amount consistency

    Policy-driven thresholds are loaded from data/policies.yaml.
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        self._policies = get_policies().validation

    def run(self, state: PipelineState) -> PipelineState:
        """
        Execute validation stage.

        Args:
            state: Pipeline state with invoice populated.

        Returns:
            Updated state with validation_result populated.
        """
        logger = AgentLogger("validation", state.run_id, state)
        logger.started({"invoice_number": state.invoice.invoice_number if state.invoice else None})

        if not state.invoice:
            state.error = "No invoice to validate"
            state.error_stage = "validation"
            logger.error("No invoice to validate")
            return state

        try:
            result = self._validate_invoice(state.invoice, logger)
            state.validation_result = result

            logger.completed({
                "is_valid": result.is_valid,
                "flag_count": len(result.flags),
                "flags": [f.flag.value for f in result.flags],
            })

        except Exception as e:
            state.error = str(e)
            state.error_stage = "validation"
            logger.error(str(e))

        return state

    def _validate_invoice(self, invoice: Invoice, logger: AgentLogger) -> ValidationResult:
        """Run all validation checks on the invoice."""
        flags: list[FlagDetail] = []
        validated_items: dict[str, dict] = {}

        # Check for missing vendor
        if invoice.vendor.is_empty:
            flags.append(FlagDetail(
                flag=ValidationFlag.MISSING_VENDOR,
                message="Vendor name is missing or empty",
                severity="error",
            ))
        else:
            # Check vendor status
            vendor_result = check_vendor(invoice.vendor.name, self.db_path) if self.db_path else check_vendor(invoice.vendor.name)

            if vendor_result.is_blacklisted:
                flags.append(FlagDetail(
                    flag=ValidationFlag.BLACKLISTED_VENDOR,
                    message=f"Vendor '{invoice.vendor.name}' is blacklisted",
                    severity="error",
                ))

        # Check for missing due date
        if invoice.due_date is None:
            flags.append(FlagDetail(
                flag=ValidationFlag.MISSING_DUE_DATE,
                message="Due date is missing",
                severity="warning",
            ))

        # Check for foreign currency
        if invoice.currency != "USD":
            flags.append(FlagDetail(
                flag=ValidationFlag.FOREIGN_CURRENCY,
                message=f"Invoice is in {invoice.currency}, not USD",
                severity="warning",
            ))

        # Check for high value (using policy threshold)
        if invoice.total > self._policies.high_value_threshold:
            flags.append(FlagDetail(
                flag=ValidationFlag.HIGH_VALUE,
                message=f"Invoice total ${invoice.total:,.2f} exceeds ${self._policies.high_value_threshold:,.0f} threshold",
                severity="warning",
            ))

        # Check for duplicate invoice
        dup_check = check_duplicate_invoice(invoice.invoice_number, self.db_path) if self.db_path else check_duplicate_invoice(invoice.invoice_number)
        if dup_check:
            flags.append(FlagDetail(
                flag=ValidationFlag.DUPLICATE_INVOICE,
                message=f"Invoice {invoice.invoice_number} was already processed on {dup_check['processed_at']}",
                severity="error",
            ))

        # Aggregate quantities by item
        aggregated = invoice.get_aggregated_quantities()

        # Validate each item
        for item_name, total_qty in aggregated.items():
            # Check for negative quantity
            if total_qty < 0:
                flags.append(FlagDetail(
                    flag=ValidationFlag.NEGATIVE_QTY,
                    message=f"Item '{item_name}' has negative quantity: {total_qty}",
                    item=item_name,
                    severity="error",
                ))
                continue

            # Look up item
            lookup_kwargs = {"db_path": self.db_path} if self.db_path else {}
            lookup = lookup_item(item_name, **lookup_kwargs)

            if not lookup.found:
                flags.append(FlagDetail(
                    flag=ValidationFlag.UNKNOWN_ITEM,
                    message=f"Item '{item_name}' not found in inventory",
                    item=item_name,
                    severity="error",
                ))
                validated_items[item_name] = {"found": False, "requested": total_qty}
                continue

            # Track fuzzy match
            if lookup.fuzzy_matched:
                flags.append(FlagDetail(
                    flag=ValidationFlag.FUZZY_MATCH,
                    message=f"Item '{item_name}' matched to '{lookup.item_name}' (score: {lookup.match_score:.0f}%)",
                    item=item_name,
                    severity="warning",
                ))

            # Check stock
            stock_kwargs = {"db_path": self.db_path} if self.db_path else {}
            stock_result = check_stock(lookup.item_name, total_qty, **stock_kwargs)
            validated_items[item_name] = {
                "found": True,
                "matched_name": lookup.item_name,
                "stock": stock_result["stock"],
                "requested": total_qty,
                "fuzzy_matched": lookup.fuzzy_matched,
            }

            if stock_result.get("error") == "ZERO_STOCK":
                flags.append(FlagDetail(
                    flag=ValidationFlag.ZERO_STOCK,
                    message=f"Item '{lookup.item_name}' has zero stock",
                    item=item_name,
                    severity="error",
                ))
            elif stock_result.get("error") == "STOCK_EXCEEDED":
                flags.append(FlagDetail(
                    flag=ValidationFlag.STOCK_EXCEEDED,
                    message=f"Item '{lookup.item_name}': requested {total_qty}, only {stock_result['stock']} in stock",
                    item=item_name,
                    severity="error",
                ))

        # Check amount consistency (line items vs total)
        computed = invoice.computed_subtotal
        if invoice.subtotal is not None and abs(computed - invoice.subtotal) > 0.01:
            flags.append(FlagDetail(
                flag=ValidationFlag.AMOUNT_MISMATCH,
                message=f"Computed subtotal ${computed:,.2f} differs from stated ${invoice.subtotal:,.2f}",
                severity="warning",
            ))

        # Check for fraud indicators
        fraud_indicators = self._check_fraud_indicators(invoice)
        if fraud_indicators:
            flags.append(FlagDetail(
                flag=ValidationFlag.FRAUD_SUSPECT,
                message=f"Fraud indicators detected: {', '.join(fraud_indicators)}",
                severity="error",
            ))

        # Determine overall validity
        is_valid = not any(f.severity == "error" for f in flags)

        return ValidationResult(
            invoice_number=invoice.invoice_number,
            is_valid=is_valid,
            flags=flags,
            validated_items=validated_items,
        )

    def _check_fraud_indicators(self, invoice: Invoice) -> list[str]:
        """Check for common fraud patterns using policy-driven rules."""
        indicators = []

        # Check vendor name for suspicious keywords (from policies)
        vendor_lower = invoice.vendor.name.lower()
        for word in self._policies.suspicious_vendor_keywords:
            if word.lower() in vendor_lower:
                indicators.append(f"suspicious vendor name contains '{word}'")

        # Check notes for urgency pressure (from policies)
        if invoice.notes:
            notes_lower = invoice.notes.lower()
            for word in self._policies.urgency_pressure_keywords:
                if word.lower() in notes_lower:
                    indicators.append(f"urgency pressure: '{word}'")
                    break

        # Unusually high single-item invoice (threshold from policies)
        threshold = self._policies.single_item_high_value_threshold
        if len(invoice.line_items) == 1 and invoice.total > threshold:
            indicators.append(f"single high-value item over ${threshold:,.0f}")

        return indicators
