"""Approval agent: makes approval decisions with generator/critic loop."""

from src.llm.client import LLMClient
from src.models.invoice import (
    ApprovalDecision,
    ApprovalStatus,
    CritiqueResult,
    ValidationFlag,
    ValidationResult,
)
from src.models.pipeline import PipelineState
from src.utils.logging import AgentLogger


class ApprovalAgent:
    """
    Agent responsible for making approval decisions.

    Uses a two-phase approach:
    1. Rule-based checks for hard requirements
    2. Generator/critic loop for nuanced decisions
    """

    MAX_CRITIQUE_ROUNDS = 3

    # Flags that trigger immediate rejection
    HARD_REJECT_FLAGS = {
        ValidationFlag.UNKNOWN_ITEM,
        ValidationFlag.STOCK_EXCEEDED,
        ValidationFlag.ZERO_STOCK,
        ValidationFlag.NEGATIVE_QTY,
        ValidationFlag.FRAUD_SUSPECT,
        ValidationFlag.BLACKLISTED_VENDOR,
        ValidationFlag.MISSING_VENDOR,
    }

    # Flags that require human review
    HUMAN_REVIEW_FLAGS = {
        ValidationFlag.FOREIGN_CURRENCY,
        ValidationFlag.DUPLICATE_INVOICE,
    }

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def run(self, state: PipelineState) -> PipelineState:
        """
        Execute approval stage.

        Args:
            state: Pipeline state with validation_result populated.

        Returns:
            Updated state with approval_decision populated.
        """
        logger = AgentLogger("approval", state.run_id, state)
        logger.started({
            "invoice_number": state.invoice.invoice_number if state.invoice else None,
            "validation_flags": (
                [f.flag.value for f in state.validation_result.flags]
                if state.validation_result else []
            ),
        })

        if not state.invoice or not state.validation_result:
            state.error = "Missing invoice or validation result"
            state.error_stage = "approval"
            logger.error("Missing invoice or validation result")
            return state

        try:
            decision = self._make_decision(
                state.invoice,
                state.validation_result,
                logger,
            )
            state.approval_decision = decision
            state.approval_critique_rounds = len(decision.critique_history)

            logger.completed({
                "status": decision.status.value,
                "reasoning": decision.reasoning[:200],
                "rules_applied": decision.rules_applied,
                "critique_rounds": len(decision.critique_history),
            })

        except Exception as e:
            state.error = str(e)
            state.error_stage = "approval"
            logger.error(str(e))

        return state

    def _make_decision(
        self,
        invoice: "Invoice",  # noqa: F821
        validation: ValidationResult,
        logger: AgentLogger,
    ) -> ApprovalDecision:
        """Make approval decision using rules and LLM."""
        rules_applied = []
        flag_set = {f.flag for f in validation.flags}

        # Phase 1: Rule-based checks

        # Check for hard reject flags
        hard_flags = flag_set & self.HARD_REJECT_FLAGS
        if hard_flags:
            rules_applied.append("hard_reject_flags")
            return ApprovalDecision(
                invoice_number=invoice.invoice_number,
                status=ApprovalStatus.REJECTED,
                reasoning=f"Rejected due to validation failures: {', '.join(f.value for f in hard_flags)}",
                rules_applied=rules_applied,
            )

        # Check for human review flags
        human_flags = flag_set & self.HUMAN_REVIEW_FLAGS
        if human_flags:
            rules_applied.append("human_review_flags")
            return ApprovalDecision(
                invoice_number=invoice.invoice_number,
                status=ApprovalStatus.NEEDS_HUMAN,
                reasoning=f"Requires human review due to: {', '.join(f.value for f in human_flags)}",
                rules_applied=rules_applied,
                escalation_reason=f"Flags requiring human review: {', '.join(f.value for f in human_flags)}",
            )

        # Check high value threshold
        if ValidationFlag.HIGH_VALUE in flag_set:
            rules_applied.append("high_value_scrutiny")

        # Check fuzzy match (conditional approval)
        if ValidationFlag.FUZZY_MATCH in flag_set:
            rules_applied.append("fuzzy_match_conditional")

        # Phase 2: Generator/Critic loop for final decision
        decision = self._generator_critic_loop(invoice, validation, rules_applied, logger)

        return decision

    def _generator_critic_loop(
        self,
        invoice: "Invoice",  # noqa: F821
        validation: ValidationResult,
        rules_applied: list[str],
        logger: AgentLogger,
    ) -> ApprovalDecision:
        """Run generator/critic loop for nuanced decision making."""
        critique_history: list[CritiqueResult] = []

        # Generate initial decision
        proposed_decision = self._generate_decision(invoice, validation, rules_applied)

        for round_num in range(self.MAX_CRITIQUE_ROUNDS):
            # Critique the decision
            critique = self._critique_decision(
                invoice,
                validation,
                proposed_decision,
                rules_applied,
            )

            critique_history.append(critique)

            if critique.accepted:
                # Critic accepts the decision
                return ApprovalDecision(
                    invoice_number=invoice.invoice_number,
                    status=proposed_decision["status"],
                    reasoning=proposed_decision["reasoning"],
                    critique_history=critique_history,
                    rules_applied=rules_applied,
                )

            # Critic rejected - refine the decision
            logger.event("critique_rejected", {
                "round": round_num + 1,
                "critique": critique.reasoning,
                "suggested_changes": critique.suggested_changes,
            })

            proposed_decision = self._refine_decision(
                invoice,
                validation,
                proposed_decision,
                critique,
                rules_applied,
            )

        # Max rounds exceeded - escalate to human
        return ApprovalDecision(
            invoice_number=invoice.invoice_number,
            status=ApprovalStatus.NEEDS_HUMAN,
            reasoning=f"Could not reach consensus after {self.MAX_CRITIQUE_ROUNDS} rounds",
            critique_history=critique_history,
            rules_applied=rules_applied,
            escalation_reason="Generator/critic loop did not converge",
        )

    def _generate_decision(
        self,
        invoice: "Invoice",  # noqa: F821
        validation: ValidationResult,
        rules_applied: list[str],
    ) -> dict:
        """Generate an initial approval decision using LLM."""
        system_prompt = """You are an invoice approval expert. Analyze the invoice and validation results to make an approval decision.

Consider:
- Validation flags and their severity
- Invoice amount and risk level
- Vendor relationship
- Overall data quality

Return JSON with:
- status: "APPROVED" or "REJECTED"
- reasoning: detailed explanation of your decision
"""

        user_content = f"""Invoice: {invoice.invoice_number}
Vendor: {invoice.vendor.name}
Total: ${invoice.total:,.2f}
Currency: {invoice.currency}
Line Items: {len(invoice.line_items)}

Validation Result:
- Valid: {validation.is_valid}
- Flags: {[f.flag.value + ': ' + f.message for f in validation.flags]}

Rules already applied: {rules_applied}

Make your approval decision."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        response = self.llm.chat_json(messages)

        return {
            "status": ApprovalStatus(response.get("status", "REJECTED")),
            "reasoning": response.get("reasoning", "No reasoning provided"),
        }

    def _critique_decision(
        self,
        invoice: "Invoice",  # noqa: F821
        validation: ValidationResult,
        proposed: dict,
        rules_applied: list[str],
    ) -> CritiqueResult:
        """Critique a proposed decision using LLM."""
        system_prompt = """You are a critical reviewer of invoice approval decisions. Your job is to find flaws in the proposed decision.

Check that the decision:
1. Properly accounts for all validation flags
2. Follows the approval rules
3. Has sound reasoning
4. Doesn't miss any red flags

Return JSON with:
- accepted: boolean (true if decision is sound, false if it needs revision)
- reasoning: explanation of your critique
- suggested_changes: string with improvements (null if accepted)
"""

        user_content = f"""Invoice: {invoice.invoice_number}
Vendor: {invoice.vendor.name}
Total: ${invoice.total:,.2f}

Validation Flags: {[f.flag.value for f in validation.flags]}
Rules Applied: {rules_applied}

Proposed Decision:
- Status: {proposed['status'].value}
- Reasoning: {proposed['reasoning']}

Critique this decision."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        response = self.llm.chat_json(messages)

        return CritiqueResult(
            accepted=response.get("accepted", True),
            reasoning=response.get("reasoning", "No critique provided"),
            suggested_changes=response.get("suggested_changes"),
        )

    def _refine_decision(
        self,
        invoice: "Invoice",  # noqa: F821
        validation: ValidationResult,
        previous: dict,
        critique: CritiqueResult,
        rules_applied: list[str],
    ) -> dict:
        """Refine a decision based on critique feedback."""
        system_prompt = """You are refining an invoice approval decision based on feedback.

Address the critique and produce an improved decision.

Return JSON with:
- status: "APPROVED" or "REJECTED"
- reasoning: improved explanation
"""

        user_content = f"""Invoice: {invoice.invoice_number}
Total: ${invoice.total:,.2f}
Validation Flags: {[f.flag.value for f in validation.flags]}

Previous Decision:
- Status: {previous['status'].value}
- Reasoning: {previous['reasoning']}

Critique:
- Reasoning: {critique.reasoning}
- Suggested Changes: {critique.suggested_changes}

Provide an improved decision."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        response = self.llm.chat_json(messages)

        return {
            "status": ApprovalStatus(response.get("status", "REJECTED")),
            "reasoning": response.get("reasoning", "No reasoning provided"),
        }
