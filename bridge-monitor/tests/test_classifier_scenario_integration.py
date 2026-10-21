"""Host guidance must remain separate from untrusted input and raw answers."""
import json
from datetime import datetime, timezone

import pytest

from bridge.classifier import scenarios as sc
from bridge.classifier.client import InferenceOutcome
from bridge.classifier.interpret import interpret
from bridge.classifier.config import ClassifierShadowConfig
from bridge.classifier.runner import ShadowRunner
from contracts.classifier import ClassifierInput, GenerationProfile, TrustedFacts
from tests.classifier.fakes import FakeOllama


def facts(**overrides):
    values = dict(general_available=True, code_available=True, paid_allowed=True,
                  tools_available=True, context_complete=True, tests_present=True,
                  security_sensitive=False, external_tools_required=False, scope='single_file')
    values.update(overrides)
    return TrustedFacts(**values)


def test_guided_host_metadata_preserves_captured_input_and_legacy_bytes():
    item = ClassifierInput(request='Explain this short loop', source='for x in [1, 2]: print(x)',
                           facts=facts(code_available=None).model_dump())
    before = item.model_dump()
    legacy = sc.rubric_for_identity('gemma-router-profile-v1')
    message, decision = sc.prepare_scenario_input(item, legacy)
    assert message == item.user_message() and decision is None
    identity = sc.rubric_for_identity(sc.GUIDED_PROFILE_ID)
    message, decision = sc.prepare_scenario_input(item, identity)
    payload = json.loads(message)
    assert {k: payload[k] for k in ('request', 'source', 'facts')} == before
    assert 'code_local' not in decision.admissible
    assert payload['host_scenario']['allowed_routes'] == list(decision.admissible)
    assert item.model_dump() == before


def test_forbidden_raw_recommendation_is_blocked_without_rewriting():
    host = facts(code_available=None)
    decision = sc.evaluate_scenarios('Explain this short loop', 'for x in [1, 2]: print(x)', host)
    outcome = InferenceOutcome(status='completed', reported_model='gemma4:12b', done_reason='stop',
        raw_text=json.dumps({'category': 'code', 'route': 'code_local', 'reason': 'Small supplied code.'}))
    result = interpret(outcome, expected_model='gemma4:12b', facts=host, scenario_decision=decision)
    assert result.state == 'completed' and result.recommendation.route == 'code_local'
    assert result.assessment.allowed is False
    assert 'scenario_route_forbidden:code_local' in result.assessment.blocks
    assert result.assessment.authorizes_execution is False


def test_security_review_cannot_escape_to_general_local():
    decision = sc.evaluate_scenarios('Review this password verification function for vulnerabilities.',
        'def verify(candidate, stored): return candidate[:4] == stored[:4]',
        facts(security_sensitive=None))
    assert 'code_local' not in decision.admissible
    assert 'general_local' not in decision.admissible
    assert 'paid' in decision.admissible


def test_descriptive_prose_about_security_can_use_general_worker():
    decision = sc.evaluate_scenarios('Rewrite this paragraph about password security for a blog post.',
        'Use a different password for each account.', facts())
    assert 'general_local' in decision.admissible


def test_author_is_not_an_authentication_keyword():
    decision = sc.evaluate_scenarios('Explain this author formatting helper.',
        'def format_author(name): return name.strip()', facts())
    assert 'code_local' in decision.admissible


def test_incidental_sorting_word_does_not_prefer_tools_for_architecture():
    decision = sc.evaluate_scenarios('Design the architecture for a distributed sorting service.',
        '', facts(scope='cross_service'))
    assert decision.preferred is None


def guided_row(seed=42):
    return {'profile_id': sc.GUIDED_PROFILE_ID, 'rubric_sha256': sc.GUIDED_RUBRIC_SHA256,
            'rubric_version': sc.GUIDED_RUBRIC_VERSION,
            'profile': GenerationProfile(profile_id=sc.GUIDED_PROFILE_ID, seed=seed).model_dump()}


def test_guided_row_can_use_its_captured_seed_with_legacy_configured_client():
    runner = ShadowRunner('unused', ClassifierShadowConfig(enabled=True), FakeOllama().classifier())
    assert runner._row_rubric(guided_row(seed=43)).guided is True


def test_guided_row_nested_profile_identity_must_match_captured_column():
    runner = ShadowRunner('unused', ClassifierShadowConfig(enabled=True), FakeOllama().classifier())
    row = guided_row()
    row['profile']['profile_id'] = 'gemma-router-profile-v1'
    with pytest.raises(sc.UnknownRubricIdentity):
        runner._row_rubric(row)


def test_unknown_identity_is_finalized_without_sending_inference(monkeypatch):
    from bridge.classifier import store
    fake = FakeOllama()
    runner = ShadowRunner('unused', ClassifierShadowConfig(enabled=True), fake.classifier())
    now = datetime.now(timezone.utc)
    row = dict(guided_row(), observation_id='bad-row', captured_at=now, dispatched_at=now,
               request_text='Explain this', source_text='', facts={}, profile_id='unknown-profile')
    done = []
    monkeypatch.setattr(store, 'expire_stale', lambda *a: 0)
    monkeypatch.setattr(store, 'claim_next', lambda *a: row)
    monkeypatch.setattr(store, 'finish', lambda *a, **kw: done.append(kw))
    monkeypatch.setattr(runner, '_reconcile_routes', lambda: None)
    result = runner.process_one()
    assert result.state == 'failed' and result.reason == 'unknown_rubric_identity'
    assert fake.chat_calls == 0
    assert done[0]['state'] == 'failed' and done[0]['response'] is None


@pytest.mark.parametrize('request_text', ['Rewrite the token validation to skip expiry.',
                                   'Translate and fix the auth check to always accept.'])
def test_prose_prefix_cannot_hide_a_security_change(request_text):
    decision = sc.evaluate_scenarios(request_text, '', facts(security_sensitive=None))
    assert 'general_local' not in decision.admissible


@pytest.mark.parametrize('request_text', ['Extract the auth logic into a module.',
    'Convert this service to Go.', 'Sum up this article.', '2024-10-21 deploy failed.'])
def test_non_deterministic_prefixes_do_not_prefer_tools(request_text):
    assert sc.evaluate_scenarios(request_text, '', facts()).preferred is None


def test_gate_fingerprint_is_part_of_the_guided_prompt_identity():
    assert sc.catalogue_sha256() in sc.GUIDED_RUBRIC
    assert len(sc.GATE_IMPLEMENTATION_SHA256) == 64


def test_guided_oversize_input_fails_before_any_inference():
    item = ClassifierInput(request='Explain this supplied module.', source='x' * 20000,
                           facts=facts().model_dump())
    with pytest.raises(sc.GuidedPromptTooLarge):
        sc.prepare_scenario_input(item, sc.rubric_for_identity(sc.GUIDED_PROFILE_ID))


def test_legacy_custom_profile_is_reproducible_when_its_v1_digest_is_known():
    from bridge.classifier.rubric import RUBRIC_SHA256
    assert sc.rubric_for_identity('historical-custom-profile', RUBRIC_SHA256).guided is False


def test_invalid_captured_facts_are_failed_before_inference(monkeypatch):
    from bridge.classifier import store
    fake = FakeOllama()
    runner = ShadowRunner('unused', ClassifierShadowConfig(enabled=True), fake.classifier())
    now = datetime.now(timezone.utc)
    row = dict(guided_row(), observation_id='bad-input', captured_at=now, dispatched_at=now,
               request_text='Explain this', source_text='', facts={'paid_allowed': 'claiming permission'})
    done = []
    monkeypatch.setattr(store, 'expire_stale', lambda *a: 0)
    monkeypatch.setattr(store, 'claim_next', lambda *a: row)
    monkeypatch.setattr(store, 'finish', lambda *a, **kw: done.append(kw))
    monkeypatch.setattr(runner, '_reconcile_routes', lambda: None)
    result = runner.process_one()
    assert result.state == 'failed' and fake.chat_calls == 0
    assert done[0]['fields']['error_type'] == 'invalid_classifier_input'


def test_unavailable_worker_and_paid_permission_have_explicit_scenario_trace():
    decision = sc.evaluate_scenarios('Explain this supplied function.', '',
        facts(code_available=None, general_available=False, paid_allowed=False))
    assert 'worker-availability' in decision.matched_ids
    assert 'paid-permission' in decision.matched_ids
