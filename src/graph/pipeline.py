"""Pipeline orchestration for invoice processing.

This module provides a simple state machine that mirrors LangGraph's interface.
When LangGraph becomes available in the environment, this can be swapped out
for the real LangGraph implementation with minimal changes.
"""

import uuid
from datetime import datetime
from typing import Callable, Protocol

from src.agents.approval import ApprovalAgent
from src.agents.ingestion import IngestionAgent
from src.agents.payment import PaymentAgent
from src.agents.validation import ValidationAgent
from src.llm.client import LLMClient, get_client
from src.models.pipeline import PipelineState


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
        for stage_name, stage_fn in self.stages:
            state = stage_fn(state)

            # Check for errors and early exit
            if state.error:
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

        return state


def create_pipeline(
    llm_client: LLMClient | None = None,
    db_path: str | None = None,
) -> Pipeline:
    """
    Create the invoice processing pipeline.

    Args:
        llm_client: LLM client to use. Defaults to get_client().
        db_path: Path to inventory database.

    Returns:
        Configured Pipeline instance.
    """
    client = llm_client or get_client()

    # Initialize agents
    ingestion_agent = IngestionAgent(client)
    validation_agent = ValidationAgent(db_path)
    approval_agent = ApprovalAgent(client)
    payment_agent = PaymentAgent(db_path=db_path)

    return SimplePipeline(
        ingestion=ingestion_agent,
        validation=validation_agent,
        approval=approval_agent,
        payment=payment_agent,
    )


def run_pipeline(
    invoice_path: str,
    llm_client: LLMClient | None = None,
    db_path: str | None = None,
    run_id: str | None = None,
) -> PipelineState:
    """
    Run the invoice processing pipeline on a single file.

    Args:
        invoice_path: Path to the invoice file.
        llm_client: LLM client to use. Defaults to get_client().
        db_path: Path to inventory database.
        run_id: Optional run ID. Generated if not provided.

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
    pipeline = create_pipeline(llm_client, db_path)
    final_state = pipeline.invoke(initial_state)

    return final_state


def run_batch(
    invoice_dir: str,
    llm_client: LLMClient | None = None,
    db_path: str | None = None,
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
        )
        results.append(result)

    return results
