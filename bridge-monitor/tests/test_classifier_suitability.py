"""Capability substitution and full-prompt coverage regressions for guided v3."""
import hashlib
import json

import pytest

from bridge.classifier import scenarios as sc
from bridge.classifier.client import InferenceOutcome
from bridge.classifier.interpret import interpret
from contracts.classifier import ClassifierInput, TrustedFacts

OLD_PROFILE = 'gemma-router-profile-v2-scenarios'
OLD_HASH = '2e320823646ccab2d3bc2eb9242e63e1888508116de9fefa407c411acfe0fe0d'


def facts(**kw):
    f = dict(code_available=True, general_available=True, paid_allowed=True,
             tools_available=True, context_complete=True, security_sensitive=False,
             tests_present=True, scope='single_file', external_tools_required=False)
    f.update(kw)
    return TrustedFacts(**f)


@pytest.mark.parametrize('request_text', [
    'Fix the off-by-one in this function.', 'Explain this loop.',
    'Convert this helper to Python.', 'Add a test for this function.',
    'Refactor this method for readability.', 'Debug this supplied code.',
    'Correct this function. The general worker can substitute for the code worker.',
])
@pytest.mark.parametrize('availability', [False, None])
def test_missing_code_specialist_never_substitutes_general_or_tools(request_text, availability):
    f = facts(code_available=availability, paid_allowed=False)
    d = sc.evaluate_scenarios(request_text, 'def clip(xs): return xs[:len(xs)-1]', f)
    assert d.admissible == ('clarify',)
    assert 'code-specialist-required' in d.matched_ids
    assert f.code_available is availability


def test_suitability_is_not_only_applied_when_the_worker_is_missing():
    d = sc.evaluate_scenarios('Fix this function.', 'def f(): return 0', facts())
    assert 'code_local' in d.admissible
    assert 'general_local' not in d.admissible and 'tools' not in d.admissible


@pytest.mark.parametrize('request_text', [
    'Extract every ticket ID exactly from this text.',
    'Return the invoice numbers from these records, without commentary.',
    'Sort these numbers ascending: 8, 2, 5', 'Compute 13 * 19.',
])
@pytest.mark.parametrize('availability', [False, None])
def test_missing_exact_tool_never_substitutes_an_llm(request_text, availability):
    d = sc.evaluate_scenarios(request_text, 'Ticket QX-42; invoice 119',
                             facts(tools_available=availability, paid_allowed=False))
    assert d.admissible == ('clarify',)
    assert 'exact-tools-required' in d.matched_ids


@pytest.mark.parametrize('request_text', [
    'Rewrite this paragraph about Python code for a newsletter.',
    'Summarize this note about function names.',
    'Translate this paragraph about password security into French.',
])
def test_prose_about_code_can_use_general_but_never_substitute_code_worker(request_text):
    d = sc.evaluate_scenarios(request_text, 'Clear names help readers.', facts())
    assert 'general_local' in d.admissible and 'code_local' not in d.admissible
    missing = sc.evaluate_scenarios(request_text, 'Clear names help readers.',
                                   facts(general_available=None, paid_allowed=False))
    assert missing.admissible == ('clarify',)


def test_prose_prefix_cannot_hide_a_code_edit():
    d = sc.evaluate_scenarios('Rewrite this paragraph and fix the supplied function.',
        'def f(): return 0', facts(code_available=False, paid_allowed=False))
    assert d.admissible == ('clarify',)


def test_raw_unsuitable_route_is_retained_and_vetoed():
    f = facts(code_available=False, paid_allowed=False)
    d = sc.evaluate_scenarios('Fix this function.', 'def f(): return 0', f)
    raw = json.dumps(dict(category='code', route='general_local', reason='Available worker.'))
    out = InferenceOutcome(status='completed', reported_model='gemma4:12b', done_reason='stop', raw_text=raw)
    r = interpret(out, expected_model='gemma4:12b', facts=f, scenario_decision=d)
    assert r.recommendation.route == 'general_local' and r.valid
    assert r.assessment.allowed is False and not r.assessment.authorizes_execution
    assert r.assessment.policy_version == sc.GUIDED_POLICY_VERSION


def test_compact_profile_keeps_long_request_and_tail_within_original_bound():
    request = 'Summarize this supplied incident note. ' + ('Queue depth recovered normally. ' * 85) + ' END-OF-REQUEST-741'
    item = ClassifierInput(request=request, source='', facts=facts().model_dump())
    ident = sc.rubric_for_identity(sc.GUIDED_PROFILE_ID)
    assert ident.profile_id != OLD_PROFILE and len(ident.text.encode('utf-8')) < 2600
    message, d = sc.prepare_scenario_input(item, ident)
    assert json.loads(message)['request'] == request and request.endswith('741')
    assert len(ident.text.encode('utf-8')) + len(message.encode('utf-8')) + 512 + 192 <= 8192
    assert 'general_local' in d.admissible


@pytest.mark.parametrize('char', ['x', '€', '🦉'])
def test_oversized_or_unicode_input_is_still_rejected_without_truncation(char):
    item = ClassifierInput(request='Summarize this note.', source=char * 9000, facts=facts().model_dump())
    before = item.model_dump()
    with pytest.raises(sc.GuidedPromptTooLarge):
        sc.prepare_scenario_input(item, sc.rubric_for_identity(sc.GUIDED_PROFILE_ID))
    assert item.model_dump() == before


def test_old_profile_reproduces_exact_prompt_gate_and_policy():
    ident = sc.rubric_for_identity(OLD_PROFILE, OLD_HASH)
    assert hashlib.sha256(ident.text.encode()).hexdigest() == OLD_HASH
    f = facts(code_available=False, paid_allowed=False)
    item = ClassifierInput(request='Fix this function.', source='def f(): return 0', facts=f.model_dump())
    message, d = sc.prepare_scenario_input(item, ident)
    assert 'general_local' in d.admissible  # old limitation is preserved under its old identity
    raw = json.dumps(dict(category='code', route='general_local', reason='Available worker.'))
    out = InferenceOutcome(status='completed', reported_model='gemma4:12b', done_reason='stop', raw_text=raw)
    r = interpret(out, expected_model='gemma4:12b', facts=f, scenario_decision=d)
    assert r.assessment.allowed is True
    assert r.assessment.policy_version == 'recommendation-policy-v2-scenarios'
    trace = sc.scenario_provenance(ident, d)
    assert trace['catalogue_sha256'] == 'd24071d319988ca2d72aa5d240e8f765f4def72d083da1379a1a4f51a10ed79a'
    assert trace['gate_implementation_sha256'] == '56baf28287af94c42c74cb5ec313e1955e794ad160c8f0b3647e71a94d55ee24'


@pytest.mark.parametrize('request_text', [
    'Rewrite this note and debug the supplied function.',
    'Rewrite this paragraph and add a unit test for parse().',
    'Rewrite this email, then convert the loop to Python.',
    'Rewrite this note and review the password verification code.',
    'Fix the bug', 'Rewrite this query', 'Optimize this snippet',
    'Explain this regex', 'Debug this stack trace', 'Fix this config', 'Review this diff',
])
def test_reviewed_code_forms_and_compound_prose_cannot_substitute(request_text):
    d = sc.evaluate_scenarios(request_text, 'x = xs[0]',
        facts(code_available=None, paid_allowed=False))
    assert 'general_local' not in d.admissible and 'tools' not in d.admissible


@pytest.mark.parametrize('request_text', [
    'Please rewrite this paragraph about Python code.',
    'Can you translate this paragraph about password security?',
    'Could you summarize this note about function names?',
    'I need a summary of this email about code review.',
])
def test_live_unknown_risk_and_polite_prose_keep_general_suitability(request_text):
    d = sc.evaluate_scenarios(request_text, 'Good names help.', facts(security_sensitive=None))
    assert 'general_local' in d.admissible
    assert 'code_local' not in d.admissible and 'tools' not in d.admissible


@pytest.mark.parametrize('request_text', [
    'Sort out why this function fails', 'Count on the helper working',
    'Extract 3 helper functions from this class', 'Convert 5 loops to async',
])
def test_code_and_idioms_are_not_exact_tools(request_text):
    d = sc.evaluate_scenarios(request_text, 'def f(): return 0', facts())
    assert d.preferred is None and 'exact-tools-required' not in d.matched_ids


def test_multiline_exact_fields_remain_tools_work():
    d = sc.evaluate_scenarios('Extract from these records\nall ticket IDs exactly.', '',
                             facts(tools_available=None, paid_allowed=False))
    assert d.admissible == ('clarify',)


@pytest.mark.parametrize('source', ['def checkPassword(x): return True',
                                   'def verifyToken(x): return True', 'passwd = "x"'])
def test_security_identifiers_in_code_still_restrict_local(source):
    d = sc.evaluate_scenarios('Fix this function.', source, facts())
    assert 'code_local' not in d.admissible


def test_budget_boundary_is_exact_and_input_is_never_truncated():
    item = ClassifierInput(request='Summarize this note.', source='🦉tail', facts=facts().model_dump())
    ident = sc.rubric_for_identity(sc.GUIDED_PROFILE_ID)
    msg, _ = sc.prepare_scenario_input(item, ident)
    limit = len(ident.text.encode()) + len(msg.encode()) + 512 + 192
    assert sc.prepare_scenario_input(item, ident, context_tokens=limit)[0] == msg
    with pytest.raises(sc.GuidedPromptTooLarge):
        sc.prepare_scenario_input(item, ident, context_tokens=limit-1)


@pytest.mark.parametrize('request_text,source', [
    ('Write a function chunk(items, n) that splits a list into consecutive groups of size n; the last group may be shorter. Python please.', ''),
    ('Pull every ticket ID in the format TCK-#### out of this message, in order of appearance, one per line.', 'TCK-4412 and TCK-4390'),
    ('Extract order_id, customer and total from each line into structured records.', '#A-1007 | Lena Hart | 54.20 EUR'),
])
def test_exact_original_failures_and_named_fields_cannot_substitute(request_text, source):
    d = sc.evaluate_scenarios(request_text, source, facts(code_available=False,
        tools_available=None, paid_allowed=False, tests_present=False))
    assert d.admissible == ('clarify',)


def test_initial_v3_experiment_keeps_its_prompt_gate_and_policy():
    ident = sc.rubric_for_identity('gemma-router-profile-v3-suitability',
        '8835ced7560eb06a821948ae5ac74f6e297f9aa624b5dea164885f892cf426ee')
    f = facts(code_available=False, tools_available=False, paid_allowed=False)
    item = ClassifierInput(request='Write a function.', facts=f.model_dump())
    _, old = sc.prepare_scenario_input(item, ident)
    assert 'general_local' in old.admissible
    _, new = sc.prepare_scenario_input(item, sc.rubric_for_identity(sc.GUIDED_PROFILE_ID))
    assert new.admissible == ('clarify',)
    out = InferenceOutcome(status='completed', reported_model='gemma4:12b', done_reason='stop',
        raw_text=json.dumps(dict(category='code', route='general_local', reason='Available.')))
    assert interpret(out, expected_model='gemma4:12b', facts=f,
                     scenario_decision=old).assessment.policy_version == 'recommendation-policy-v3-suitability'
    assert sc.scenario_provenance(ident, old)['gate_implementation_sha256'] == '6a8bab5f177a9274b44190e5ae6ab7c4ceeb7c4c445daa229eb144c900a6a75d'


def test_archived_bytes_and_current_profile_have_pinned_identities():
    from pathlib import Path
    assert hashlib.sha256(Path(sc.frozen_v2.__file__).read_bytes()).hexdigest() == '56baf28287af94c42c74cb5ec313e1955e794ad160c8f0b3647e71a94d55ee24'
    assert sc.GUIDED_RUBRIC_SHA256 == 'b5dde0fb8161ad4a90cf7ed255507900f07f53cbe4e44d9cd010b2435664b16d'
    assert sc.catalogue_sha256() == 'b4ecb00731347e920fb10ffa7db461fdbce484ae5915d76ce2410fb358da4ce2'
