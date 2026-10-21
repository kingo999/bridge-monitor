'''One interpretation of an inference outcome, shared by the runner and replay tools.'''

import dataclasses
from dataclasses import dataclass

from bridge.classifier.client import InferenceOutcome
from bridge.classifier.policy import assess_recommendation
from bridge.classifier.scenarios import ScenarioDecision, policy_version_for_decision
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


def _interpret(outcome: InferenceOutcome, *, expected_model: str,
               facts: TrustedFacts | None) -> Interpretation:
    facts = facts or TrustedFacts()
    if outcome.status == 'cancelled':
        state, error = 'cancelled', 'cancelled'
    elif outcome.status in ('timeout', 'transport_error') and outcome.sent:
        state, error = 'uncertain', outcome.error_type
    elif outcome.status in ('http_error', 'oversized', 'incomplete'):
        state, error = 'failed', outcome.error_type
    elif outcome.status != 'completed':
        state, error = 'failed', outcome.error_type or outcome.status
    else:
        state, error = 'completed', None
    if state != 'completed':
        return Interpretation(state, None, None, error, None,
                              assess_recommendation(None, facts))
    if outcome.reported_model != expected_model:
        return Interpretation('failed', None, None, 'reported_model_mismatch', None,
                              assess_recommendation(None, facts))
    if outcome.thinking_present:
        return Interpretation('failed', None, False, 'unexpected_thinking_output', None,
                              assess_recommendation(None, facts))
    try:
        rec = parse_recommendation(outcome.raw_text)
    except ClassifierContractError as exc:
        return Interpretation('failed', None, False, 'contract_invalid', exc.code,
                              assess_recommendation(None, facts))
    return Interpretation('completed', rec, True, None, None, assess_recommendation(rec, facts))


def interpret(outcome: InferenceOutcome, *, expected_model: str, facts: TrustedFacts | None,
              scenario_decision: ScenarioDecision | None = None) -> Interpretation:
    '''Partial or invalid output is a failure to observe, never a repaired success.

    With a scenario decision (guided rows only) a raw route outside the admissible set adds
    the block scenario_route_forbidden:<route>, sets allowed False and uses the guided policy
    version. The raw recommendation is never rewritten.
    '''
    result = _interpret(outcome, expected_model=expected_model, facts=facts)
    if scenario_decision is None:
        return result
    base = result.assessment
    blocks, allowed = list(base.blocks), base.allowed
    rec = result.recommendation
    if rec is not None and rec.route not in scenario_decision.admissible:
        blocks.append('scenario_route_forbidden:' + rec.route)
        allowed = False
    assessment = base.model_copy(update={'blocks': blocks, 'allowed': allowed,
                                         'policy_version': policy_version_for_decision(scenario_decision)})
    return dataclasses.replace(result, assessment=assessment)
