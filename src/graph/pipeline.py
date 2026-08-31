"""Pipeline orchestration for invoice processing.

This module provides a simple state machine that mirrors LangGraph's interface.
When LangGraph becomes available in the environment, this can be swapped out
for the real LangGraph implementation with minimal changes.

Supports optional integration with:
- AuditTrail: Immutable logging of all pipeline events
- RecoveryHandler: Retry failed operations with exponential backoff
- ReviewQueue: Human review queue for escalated invoices
- LangGraph: Real LangGraph StateGraph when available
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol, Any

import structlog

from src.agents.approval import ApprovalAgent
from src.agents.ingestion import IngestionAgent
from src.agents.payment import PaymentAgent
from src.agents.validation import ValidationAgent
from src.llm.client import LLMClient, get_client
from src.models.invoice import ApprovalStatus
from src.models.pipeline import PipelineState

log = structlog.get_logger()

# Check for LangGraph availability
LANGGRAPH_AVAILABLE = False
try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    StateGraph = None
    END = None


class Pipeline(Protocol):
    """Protocol for pipeline implementations."""

    def invoke(self, state: PipelineState) -> PipelineState:
        """Run the pipeline on the given state."""
        ...


class SimplePipeline:
    """
    Simple sequential pipeline implementation.

    Runs stages in order: ingest -> validate -> approve -> payment.
    Stops early if any stage sets an error.

    This mirrors LangGraph's StateGraph interface and can be swapped
    for the real thing when available.
    """

    def __init__(
        self,
        ingestion: IngestionAgent,
        validation: ValidationAgent,
        approval: ApprovalAgent,
        payment: PaymentAgent,
    ):
        self.stages: list[tuple[str, Callable[[PipelineState], PipelineState]]] = [
            ("ingest", ingestion.run),
            ("validate", validation.run),
            ("approve", approval.run),
            ("payment", payment.run),
        ]

    def invoke(self, state: PipelineState) -> PipelineState:
        """Run all pipeline stages sequentially."""
        log.debug("pipeline_started", run_id=state.run_id)
        
        for stage_name, stage_fn in self.stages:
            log.debug("stage_started", run_id=state.run_id, stage=stage_name)
            state = stage_fn(state)

            # Check for errors and early exit
            if state.error:
                log.warning("stage_error", run_id=state.run_id, stage=stage_name, error_type=state.error_stage)
                break

            # Check stage-specific conditions
            if stage_name == "ingest" and state.invoice is None:
                state.error = "Ingestion produced no invoice"
                state.error_stage = "ingest"
                log.warning("stage_error", run_id=state.run_id, stage="ingest", error_type="no_invoice")
                break
            elif stage_name == "validate" and state.validation_result is None:
                state.error = "Validation produced no result"
                state.error_stage = "validate"
                log.warning("stage_error", run_id=state.run_id, stage="validate", error_type="no_result")
                break
            elif stage_name == "approve" and state.approval_decision is None:
                state.error = "Approval produced no decision"
                state.error_stage = "approve"
                log.warning("stage_error", run_id=state.run_id, stage="approve", error_type="no_decision")
                break
            
            log.debug("stage_completed", run_id=state.run_id, stage=stage_name)

        log.debug("pipeline_completed", run_id=state.run_id, final_status=state.final_status)
        return state


class EnhancedPipeline:
    """
    Pipeline with audit trail, recovery, and review queue integration.

    Wraps SimplePipeline and adds:
    - Audit events at each stage transition
    - Error recording for retry handling
    - Automatic review queue insertion for escalated invoices
    """

    def __init__(
        self,
        base_pipeline: SimplePipeline,
        audit_db: str | Path | None = None,
        recovery_db: str | Path | None = None,
        review_db: str | Path | None = None,
    ):
        self._pipeline = base_pipeline
        self._audit = None
        self._recovery = None
        self._review = None

        # Lazy init to avoid creating DBs unless used
        self._audit_db = audit_db
        self._recovery_db = recovery_db
        self._review_db = review_db

    def _get_audit(self):
        if self._audit is None and self._audit_db:
            from src.audit.trail import AuditTrail
            self._audit = AuditTrail(self._audit_db)
        return self._audit

    def _get_recovery(self):
        if self._recovery is None and self._recovery_db:
            from src.recovery.handler import RecoveryHandler
            self._recovery = RecoveryHandler(self._recovery_db)
        return self._recovery

    def _get_review(self):
        if self._review is None and self._review_db:
            from src.workflow.review import ReviewQueue
            self._review = ReviewQueue(self._review_db)
        return self._review

    def _record_stage_audit(self, audit, state: PipelineState, stage_name: str) -> None:
        """Record audit event for completed stage."""
        from src.audit.trail import AuditAction, AuditEvent
        
        invoice_number = state.invoice.invoice_number if state.invoice else None
        
        if stage_name == "ingest" and state.invoice:
            audit.record(AuditEvent(
                run_id=state.run_id,
                invoice_number=invoice_number,
                action=AuditAction.INVOICE_PARSED,
                details={
                    "vendor": state.invoice.vendor.name,
                    "total": float(state.invoice.total),
                    "line_items": len(state.invoice.line_items),
                },
            ))
        elif stage_name == "validate" and state.validation_result:
            audit.record(AuditEvent(
                run_id=state.run_id,
                invoice_number=invoice_number,
                action=AuditAction.INVOICE_VALIDATED,
                details={
                    "is_valid": state.validation_result.is_valid,
                    "flags": [f.flag.value for f in state.validation_result.flags],
                },
            ))
        elif stage_name == "approve" and state.approval_decision:
            # Only record APPROVED here - REJECTED and ESCALATED are recorded in final handling
            if state.approval_decision.status.value == "APPROVED":
                audit.record(AuditEvent(
                    run_id=state.run_id,
                    invoice_number=invoice_number,
                    action=AuditAction.INVOICE_APPROVED,
                    details={
                        "status": state.approval_decision.status.value,
                        "reasoning": state.approval_decision.reasoning[:200],
                    },
                ))

    def invoke(self, state: PipelineState) -> PipelineState:
        """Run pipeline with audit/recovery/review integration."""
        log.info("enhanced_pipeline_started", run_id=state.run_id, 
                 audit_enabled=self._audit_db is not None,
                 review_enabled=self._review_db is not None)
        
        audit = self._get_audit()
        recovery = self._get_recovery()
        review = self._get_review()

        # Record invoice received
        if audit and state.source_path:
            from src.audit.trail import AuditAction, AuditEvent
            audit.record(AuditEvent(
                run_id=state.run_id,
                action=AuditAction.INVOICE_RECEIVED,
                details={"source": state.source_path},
            ))

        # Run stages individually to record audit events
        for stage_name, stage_fn in self._pipeline.stages:
            log.debug("stage_started", run_id=state.run_id, stage=stage_name)
            state = stage_fn(state)
            
            # Record stage completion in audit trail
            if audit and not state.error:
                self._record_stage_audit(audit, state, stage_name)
            
            # Check for errors
            if state.error:
                log.warning("stage_error", run_id=state.run_id, stage=stage_name)
                break
            
            # Check stage-specific conditions
            if stage_name == "ingest" and state.invoice is None:
                state.error = "Ingestion produced no invoice"
                state.error_stage = "ingest"
                break
            elif stage_name == "validate" and state.validation_result is None:
                state.error = "Validation produced no result"
                state.error_stage = "validate"
                break
            elif stage_name == "approve" and state.approval_decision is None:
                state.error = "Approval produced no decision"
                state.error_stage = "approve"
                break
            
            log.debug("stage_completed", run_id=state.run_id, stage=stage_name)

        # Handle errors - record for recovery
        if state.error:
            log.warning("pipeline_error", run_id=state.run_id, stage=state.error_stage)
        if state.error and recovery:
            recovery.record_failure(
                run_id=state.run_id,
                invoice_number=state.invoice.invoice_number if state.invoice else None,
                stage=state.error_stage or "unknown",
                error=Exception(state.error),
                context={"source": state.source_path},
            )
            if audit:
                from src.audit.trail import AuditAction, AuditEvent
                audit.record(AuditEvent(
                    run_id=state.run_id,
                    invoice_number=state.invoice.invoice_number if state.invoice else None,
                    action=AuditAction.ERROR_OCCURRED,
                    details={"error": state.error, "stage": state.error_stage},
                ))

        # Handle escalations - add to review queue
        elif state.approval_decision and state.approval_decision.status == ApprovalStatus.NEEDS_HUMAN:
            log.info("invoice_escalated", run_id=state.run_id, status="needs_human")
            if review and state.invoice:
                from src.workflow.review import ReviewItem
                review.add(ReviewItem(
                    run_id=state.run_id,
                    invoice_number=state.invoice.invoice_number,
                    vendor=state.invoice.vendor.name,
                    amount=float(state.invoice.total),
                    currency=state.invoice.currency,
                    escalation_reason=state.approval_decision.effective_escalation_reason,
                    escalation_flags=[f.flag.value for f in state.validation_result.flags] if state.validation_result else [],
                    invoice_data=state.invoice.model_dump(mode="json"),
                    validation_flags=[f.flag.value for f in state.validation_result.flags] if state.validation_result else [],
                ))
            if audit:
                from src.audit.trail import AuditAction, AuditEvent
                audit.record(AuditEvent(
                    run_id=state.run_id,
                    invoice_number=state.invoice.invoice_number if state.invoice else None,
                    action=AuditAction.INVOICE_ESCALATED,
                    details={"reason": state.approval_decision.effective_escalation_reason},
                ))

        # Record successful completion
        elif state.payment_status:
            log.info("pipeline_completed", run_id=state.run_id, status=state.payment_status)
            if audit:
                from src.audit.trail import AuditAction, AuditEvent
                action = AuditAction.PAYMENT_PROCESSED if state.payment_status == "success" else AuditAction.INVOICE_REJECTED
                audit.record(AuditEvent(
                    run_id=state.run_id,
                    invoice_number=state.invoice.invoice_number if state.invoice else None,
                    action=action,
                    details={"payment_status": state.payment_status},
                ))

        return state


class LangGraphPipeline:
    """
    LangGraph-based pipeline implementation.

    Uses real LangGraph StateGraph for orchestration when available.
    Provides conditional routing, state persistence, and streaming.
    """

    def __init__(
        self,
        ingestion: IngestionAgent,
        validation: ValidationAgent,
        approval: ApprovalAgent,
        payment: PaymentAgent,
    ):
        if not LANGGRAPH_AVAILABLE:
            raise ImportError(
                "LangGraph not installed. Install with: pip install langgraph langchain-core"
            )

        self.ingestion = ingestion
        self.validation = validation
        self.approval = approval
        self.payment = payment
        self._graph = self._build_graph()

    def _build_graph(self) -> Any:
        """Build the LangGraph StateGraph."""
        from langgraph.graph import StateGraph, END

        # Define state schema as dict for LangGraph
        def state_to_dict(state: PipelineState) -> dict:
            return state.model_dump(mode="json")

        def dict_to_state(d: dict) -> PipelineState:
            return PipelineState.model_validate(d)

        # Node functions that work with dict state
        def ingest_node(state: dict) -> dict:
            ps = dict_to_state(state)
            ps = self.ingestion.run(ps)
            return state_to_dict(ps)

        def validate_node(state: dict) -> dict:
            ps = dict_to_state(state)
            ps = self.validation.run(ps)
            return state_to_dict(ps)

        def approve_node(state: dict) -> dict:
            ps = dict_to_state(state)
            ps = self.approval.run(ps)
            return state_to_dict(ps)

        def payment_node(state: dict) -> dict:
            ps = dict_to_state(state)
            ps = self.payment.run(ps)
            return state_to_dict(ps)

        # Conditional routing
        def should_continue(state: dict) -> str:
            if state.get("error"):
                return "error"
            return "continue"

        def approval_routing(state: dict) -> str:
            if state.get("error"):
                return "error"
            decision = state.get("approval_decision", {})
            status = decision.get("status") if isinstance(decision, dict) else None
            if status == "NEEDS_HUMAN":
                return "escalate"
            return "payment"

        # Build graph
        workflow = StateGraph(dict)

        # Add nodes
        workflow.add_node("ingest", ingest_node)
        workflow.add_node("validate", validate_node)
        workflow.add_node("approve", approve_node)
        workflow.add_node("payment", payment_node)

        # Set entry point
        workflow.set_entry_point("ingest")

        # Add edges with conditions
        workflow.add_conditional_edges(
            "ingest",
            should_continue,
            {"continue": "validate", "error": END}
        )
        workflow.add_conditional_edges(
            "validate",
            should_continue,
            {"continue": "approve", "error": END}
        )
        workflow.add_conditional_edges(
            "approve",
            approval_routing,
            {"payment": "payment", "escalate": END, "error": END}
        )
        workflow.add_edge("payment", END)

        return workflow.compile()

    def invoke(self, state: PipelineState) -> PipelineState:
        """Run the LangGraph pipeline."""
        initial_state = state.model_dump(mode="json")
        final_state = self._graph.invoke(initial_state)
        return PipelineState.model_validate(final_state)

    def stream(self, state: PipelineState):
        """Stream pipeline execution for real-time updates."""
        initial_state = state.model_dump(mode="json")
        for event in self._graph.stream(initial_state):
            yield event


def create_pipeline(
    llm_client: LLMClient | None = None,
    db_path: str | None = None,
    enable_audit: bool = False,
    enable_recovery: bool = False,
    enable_review: bool = False,
    use_langgraph: bool = False,
    audit_db: str | None = None,
    recovery_db: str | None = None,
    review_db: str | None = None,
) -> Pipeline:
    """
    Create the invoice processing pipeline.

    Args:
        llm_client: LLM client to use. Defaults to get_client().
        db_path: Path to inventory database.
        enable_audit: Enable audit trail logging.
        enable_recovery: Enable error recovery/retry handling.
        enable_review: Enable review queue for escalations.
        use_langgraph: Use LangGraph for orchestration (requires langgraph package).
        audit_db: Path to audit database (default: audit.db).
        recovery_db: Path to recovery database (default: recovery.db).
        review_db: Path to review queue database (default: review.db).

    Returns:
        Configured Pipeline instance.
    """
    client = llm_client or get_client()

    # Initialize agents
    ingestion_agent = IngestionAgent(client)
    validation_agent = ValidationAgent(db_path)
    approval_agent = ApprovalAgent(client)
    payment_agent = PaymentAgent(db_path=db_path)

    # Use LangGraph if requested and available
    if use_langgraph:
        if not LANGGRAPH_AVAILABLE:
            raise ImportError(
                "LangGraph requested but not installed. "
                "Install with: pip install langgraph langchain-core"
            )
        base_pipeline = LangGraphPipeline(
            ingestion=ingestion_agent,
            validation=validation_agent,
            approval=approval_agent,
            payment=payment_agent,
        )
    else:
        base_pipeline = SimplePipeline(
            ingestion=ingestion_agent,
            validation=validation_agent,
            approval=approval_agent,
            payment=payment_agent,
        )

    # Return enhanced pipeline if any integrations enabled
    if enable_audit or enable_recovery or enable_review:
        return EnhancedPipeline(
            base_pipeline=base_pipeline,
            audit_db=audit_db or "audit.db" if enable_audit else None,
            recovery_db=recovery_db or "recovery.db" if enable_recovery else None,
            review_db=review_db or "review.db" if enable_review else None,
        )

    return base_pipeline


def run_pipeline(
    invoice_path: str,
    llm_client: LLMClient | None = None,
    db_path: str | None = None,
    run_id: str | None = None,
    enable_audit: bool = True,
    enable_recovery: bool = False,
    enable_review: bool = True,
) -> PipelineState:
    """
    Run the invoice processing pipeline on a single file.

    Args:
        invoice_path: Path to the invoice file.
        llm_client: LLM client to use. Defaults to get_client().
        db_path: Path to inventory database.
        run_id: Optional run ID. Generated if not provided.
        enable_audit: Enable audit trail logging.
        enable_recovery: Enable error recovery handling.
        enable_review: Enable review queue for escalations (default: True).

    Returns:
        Final PipelineState with all results.
    """
    # Generate run ID
    if run_id is None:
        run_id = f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

    # Create initial state
    initial_state = PipelineState(
        run_id=run_id,
        source_path=invoice_path,
    )

    # Create and run pipeline
    pipeline = create_pipeline(
        llm_client,
        db_path,
        enable_audit=enable_audit,
        enable_recovery=enable_recovery,
        enable_review=enable_review,
    )
    final_state = pipeline.invoke(initial_state)

    return final_state


def run_batch(
    invoice_dir: str,
    llm_client: LLMClient | None = None,
    db_path: str | None = None,
    enable_audit: bool = True,
    enable_recovery: bool = False,
    enable_review: bool = True,
) -> list[PipelineState]:
    """
    Run the pipeline on all invoices in a directory.

    Args:
        invoice_dir: Directory containing invoice files.
        llm_client: LLM client to use.
        db_path: Path to inventory database.

    Returns:
        List of PipelineState results.
    """
    from pathlib import Path

    invoice_path = Path(invoice_dir)
    if not invoice_path.is_dir():
        raise ValueError(f"Not a directory: {invoice_dir}")

    # Find all invoice files
    extensions = {".txt", ".pdf", ".json", ".csv", ".xml"}
    invoice_files = [
        f for f in invoice_path.iterdir()
        if f.suffix.lower() in extensions and not f.name.startswith(".")
    ]

    # Sort for consistent ordering
    invoice_files.sort()

    results = []
    for invoice_file in invoice_files:
        result = run_pipeline(
            str(invoice_file),
            llm_client=llm_client,
            db_path=db_path,
            enable_audit=enable_audit,
            enable_recovery=enable_recovery,
            enable_review=enable_review,
        )
        results.append(result)

    return results
