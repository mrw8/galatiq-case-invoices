"""Pipeline state model for LangGraph orchestration."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.models.invoice import ApprovalDecision, Invoice, ValidationResult


class PipelineState(BaseModel):
    """
    Mutable state passed through the LangGraph pipeline.

    This is the central state object that accumulates data
    as the invoice flows through ingestion -> validation -> approval -> payment.
    """

    # Run metadata
    run_id: str
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None

    # Input
    source_path: str
    raw_content: str | None = None

    # Stage outputs (populated as pipeline progresses)
    invoice: Invoice | None = None
    validation_result: ValidationResult | None = None
    approval_decision: ApprovalDecision | None = None

    # Payment result
    payment_status: str | None = None  # success, failed, skipped
    payment_reference: str | None = None

    # Error tracking
    error: str | None = None
    error_stage: str | None = None

    # Retry tracking
    ingestion_attempts: int = 0
    approval_critique_rounds: int = 0

    # Trace log for all agent events
    events: list[dict[str, Any]] = Field(default_factory=list)

    def add_event(
        self,
        agent: str,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Append an event to the trace log."""
        self.events.append({
            "timestamp": datetime.now().isoformat(),
            "run_id": self.run_id,
            "agent": agent,
            "event_type": event_type,
            "data": data or {},
        })

    @property
    def is_complete(self) -> bool:
        """Check if pipeline has finished (success or failure)."""
        return self.completed_at is not None

    @property
    def final_status(self) -> str:
        """Get the final outcome of the pipeline."""
        if self.error:
            return f"ERROR: {self.error_stage}"
        if self.payment_status:
            return self.payment_status
        if self.approval_decision:
            return self.approval_decision.status.value
        return "INCOMPLETE"

    def to_summary(self) -> dict[str, Any]:
        """Generate a human-readable summary."""
        return {
            "run_id": self.run_id,
            "source": self.source_path,
            "invoice_number": self.invoice.invoice_number if self.invoice else None,
            "vendor": self.invoice.vendor.name if self.invoice else None,
            "total": str(self.invoice.total) if self.invoice else None,
            "validation_flags": (
                [f.flag.value for f in self.validation_result.flags]
                if self.validation_result
                else []
            ),
            "approval_status": (
                self.approval_decision.status.value if self.approval_decision else None
            ),
            "payment_status": self.payment_status,
            "final_status": self.final_status,
            "duration_ms": (
                int((self.completed_at - self.started_at).total_seconds() * 1000)
                if self.completed_at
                else None
            ),
            "error": self.error,
        }
