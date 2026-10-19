"""One interpretation of an inference outcome, shared by the runner and replay tools."""

from dataclasses import dataclass

from bridge.classifier.client import InferenceOutcome
from bridge.classifier.policy import assess_recommendation
from bridge.classifier.validation import ClassifierContractError, parse_recommendation
from contracts.classifier import PolicyAssessment, Recommendation, TrustedFacts


@dataclass
class Interpretation:
    state: str  # completed | failed | uncertain | cancelled
    recommendation: Recommendation | None
    valid: bool | None  # None when no complete response exists to judge
    error_type: str | None
    validation_error: str | None
    assessment: PolicyAssessment


def interpret(outcome: InferenceOutcome, *, expected_model: str,
              facts: TrustedFacts | None) -> Interpretation:
    """Map transport/stream evidence and the parsed body to a conservative state.

    Partial or contract-invalid output is a failure to observe, never a repaired success.
    """
    facts = facts or TrustedFacts()
    if outcome.status == "cancelled":
        state, error = "cancelled", "cancelled"
    elif outcome.status in ("timeout", "transport_error") and outcome.sent:
        state, error = "uncertain", outcome.error_type
    elif outcome.status in ("http_error", "oversized", "incomplete"):
        state, error = "failed", outcome.error_type
    elif outcome.status != "completed":
        state, error = "failed", outcome.error_type or outcome.status
    else:
        state, error = "completed", None
    if state != "completed":
        return Interpretation(state, None, None, error, None,
                              assess_recommendation(None, facts))
    if outcome.reported_model != expected_model:
        return Interpretation("failed", None, None, "reported_model_mismatch", None,
                              assess_recommendation(None, facts))
    if outcome.thinking_present:
        return Interpretation("failed", None, False, "unexpected_thinking_output", None,
                              assess_recommendation(None, facts))
    try:
        rec = parse_recommendation(outcome.raw_text)
    except ClassifierContractError as exc:
        return Interpretation("failed", None, False, "contract_invalid", exc.code,
                              assess_recommendation(None, facts))
    return Interpretation("completed", rec, True, None, None, assess_recommendation(rec, facts))
