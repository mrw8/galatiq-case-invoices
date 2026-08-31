"""Payment agent: processes approved invoices or logs rejections."""

import json
import uuid
from datetime import datetime
from pathlib import Path

from src.db.queries import record_processed_invoice
from src.models.invoice import ApprovalStatus
from src.models.pipeline import PipelineState
from src.utils.logging import AgentLogger


class PaymentAgent:
    """
    Agent responsible for processing approved payments.

    - Approved invoices: calls mock payment API
    - Rejected invoices: logs to rejections file
    - Escalated invoices: queues for human review
    """

    def __init__(
        self,
        rejections_path: str = "rejections.jsonl",
        db_path: str | None = None,
    ):
        self.rejections_path = Path(rejections_path)
        self.db_path = db_path

    def run(self, state: PipelineState) -> PipelineState:
        """
        Execute payment stage.

        Args:
            state: Pipeline state with approval_decision populated.

        Returns:
            Updated state with payment_status populated.
        """
        logger = AgentLogger("payment", state.run_id, state)
        logger.started({
            "invoice_number": state.invoice.invoice_number if state.invoice else None,
            "approval_status": (
                state.approval_decision.status.value if state.approval_decision else None
            ),
        })

        if not state.invoice or not state.approval_decision:
            state.error = "Missing invoice or approval decision"
            state.error_stage = "payment"
            logger.error("Missing invoice or approval decision")
            return state

        try:
            decision = state.approval_decision

            if decision.status == ApprovalStatus.APPROVED:
                result = self._process_payment(state, logger)
                state.payment_status = result["status"]
                state.payment_reference = result.get("reference")

            elif decision.status == ApprovalStatus.REJECTED:
                self._log_rejection(state, logger)
                state.payment_status = "rejected"

            elif decision.status == ApprovalStatus.NEEDS_HUMAN:
                self._queue_for_review(state, logger)
                state.payment_status = "escalated"

            else:
                state.payment_status = "skipped"

            # Record in processed invoices
            self._record_processed(state)

            logger.completed({
                "payment_status": state.payment_status,
                "payment_reference": state.payment_reference,
            })

        except Exception as e:
            state.error = str(e)
            state.error_stage = "payment"
            logger.error(str(e))

        # Mark pipeline complete
        state.completed_at = datetime.now()

        return state

    def _process_payment(self, state: PipelineState, logger: AgentLogger) -> dict:
        """Process approved payment through mock API."""
        invoice = state.invoice

        # Idempotency check - don't double-pay
        from src.db.queries import check_duplicate_invoice

        dup = check_duplicate_invoice(invoice.invoice_number, self.db_path) if self.db_path else check_duplicate_invoice(invoice.invoice_number)
        if dup and dup.get("status") == "paid":
            logger.event("payment_skipped", {"reason": "already_paid"})
            return {"status": "skipped", "reason": "already_paid"}

        # Call mock payment API
        result = mock_payment(
            vendor=invoice.vendor.name,
            amount=float(invoice.total),
            invoice_number=invoice.invoice_number,
        )

        logger.event("payment_processed", {
            "vendor": invoice.vendor.name,
            "amount": float(invoice.total),
            "reference": result.get("reference"),
        })

        return result

    def _log_rejection(self, state: PipelineState, logger: AgentLogger) -> None:
        """Log rejected invoice to rejections file."""
        invoice = state.invoice
        decision = state.approval_decision

        rejection_record = {
            "timestamp": datetime.now().isoformat(),
            "run_id": state.run_id,
            "invoice_number": invoice.invoice_number,
            "vendor": invoice.vendor.name,
            "total": str(invoice.total),
            "rejection_reason": decision.reasoning,
            "validation_flags": (
                [f.flag.value for f in state.validation_result.flags]
                if state.validation_result else []
            ),
            "rules_applied": decision.rules_applied,
            "critique_history": [
                {"accepted": c.accepted, "reasoning": c.reasoning}
                for c in decision.critique_history
            ],
        }

        # Append to JSONL file
        with open(self.rejections_path, "a") as f:
            f.write(json.dumps(rejection_record) + "\n")

        logger.event("rejection_logged", {
            "invoice_number": invoice.invoice_number,
            "reason": decision.reasoning[:100],
        })

    def _queue_for_review(self, state: PipelineState, logger: AgentLogger) -> None:
        """Queue invoice for human review."""
        # In a real system, this would add to a review queue/database
        # For now, we just log it

        logger.event("queued_for_review", {
            "invoice_number": state.invoice.invoice_number,
            "escalation_reason": state.approval_decision.escalation_reason,
        })

    def _record_processed(self, state: PipelineState) -> None:
        """Record invoice as processed for duplicate detection."""
        invoice = state.invoice

        status_map = {
            "success": "paid",
            "rejected": "rejected",
            "escalated": "pending_review",
            "skipped": "skipped",
        }

        record_kwargs = {
            "invoice_number": invoice.invoice_number,
            "status": status_map.get(state.payment_status, state.payment_status),
            "total_amount": float(invoice.total),
            "vendor": invoice.vendor.name,
            "run_id": state.run_id,
        }

        if self.db_path:
            record_kwargs["db_path"] = self.db_path

        record_processed_invoice(**record_kwargs)


def mock_payment(vendor: str, amount: float, invoice_number: str) -> dict:
    """
    Mock payment API.

    In a real system, this would integrate with a banking/payment API.

    Args:
        vendor: Vendor to pay.
        amount: Amount to pay.
        invoice_number: Invoice reference.

    Returns:
        Payment result with status and reference.
    """
    # Generate a mock payment reference
    reference = f"PAY-{uuid.uuid4().hex[:8].upper()}"

    print(f"[MOCK PAYMENT] Paid ${amount:,.2f} to {vendor}")
    print(f"[MOCK PAYMENT] Reference: {reference}")

    return {
        "status": "success",
        "reference": reference,
        "vendor": vendor,
        "amount": amount,
        "invoice_number": invoice_number,
        "timestamp": datetime.now().isoformat(),
    }
