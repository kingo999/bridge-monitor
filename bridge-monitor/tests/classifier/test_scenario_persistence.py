"""Guided shadow evidence coexists with legacy rows without touching routing."""
import json

import psycopg
from psycopg.rows import dict_row

from bridge.classifier import ingest, store
from bridge.classifier.config import ClassifierShadowConfig
from bridge.classifier.runner import ShadowRunner
from bridge.classifier.rubric import RUBRIC, RUBRIC_SHA256
from bridge.classifier.scenarios import GUIDED_PROFILE_ID, GUIDED_RUBRIC, GUIDED_RUBRIC_SHA256
from contracts.classifier import GenerationProfile
from tests.classifier.conftest import table_counts
from tests.classifier.fakes import FakeOllama, reply_json


def test_mixed_profiles_persist_the_actual_prompt_gate_and_captured_seed(cdsn, cfg):
    before = table_counts(cdsn)
    facts = dict(general_available=True, code_available=True, paid_allowed=True,
                 tools_available=True, context_complete=True, tests_present=True,
                 security_sensitive=None, scope='single_file')
    guided = ClassifierShadowConfig(enabled=True, scenario_guidance=True,
        profile=GenerationProfile(profile_id=GUIDED_PROFILE_ID, seed=43))
    def capture(config):
        return ingest.capture_text(cdsn, config, origin_kind='replay', origin_ref='same-input',
            request='Explain this short loop.', source='for x in [1, 2]: print(x)', facts=facts)
    legacy_obs, guided_obs = capture(cfg), capture(guided)
    assert legacy_obs.observation_id != guided_obs.observation_id
    assert capture(guided).state == 'duplicate'
    fake = FakeOllama(reply_json(route='code_local', category='code'))
    with ShadowRunner(cdsn, cfg, fake.classifier()) as runner:
        first, second = runner.process_one(), runner.process_one()
    assert first.state == second.state == 'completed'
    assert fake.chat_calls == 2
    assert fake.chat_bodies[0]['messages'][0]['content'] == RUBRIC
    assert fake.chat_bodies[1]['messages'][0]['content'] == GUIDED_RUBRIC
    assert fake.chat_bodies[1]['options']['seed'] == 43
    with psycopg.connect(cdsn, row_factory=dict_row) as conn:
        rows = {r['observation_id']: r for r in conn.execute('SELECT * FROM classifier_observations')}
    legacy, guided_row = rows[legacy_obs.observation_id], rows[guided_obs.observation_id]
    assert legacy['rubric_sha256'] == RUBRIC_SHA256 and legacy['policy_allowed'] is True
    assert 'scenario' not in legacy['backend']
    assert guided_row['rubric_sha256'] == GUIDED_RUBRIC_SHA256
    assert guided_row['rec_route'] == 'code_local' and guided_row['policy_allowed'] is False
    assert 'scenario_route_forbidden:code_local' in guided_row['policy_blocks']
    trace = guided_row['backend']['scenario']
    assert trace['profile_id'] == GUIDED_PROFILE_ID
    assert trace['rubric_sha256'] == guided_row['rubric_sha256']
    assert trace['comparison']['raw_route'] == 'code_local'
    assert trace['comparison']['admitted'] is False
    assert json.loads(fake.chat_bodies[1]['messages'][1]['content'])['facts'] == facts
    assert table_counts(cdsn) == before


def test_reference_label_can_be_frozen_before_shadow_inference(cdsn):
    config = ClassifierShadowConfig(enabled=True, scenario_guidance=True,
        profile=GenerationProfile(profile_id=GUIDED_PROFILE_ID))
    capture = ingest.capture_text(cdsn, config, origin_kind='replay', origin_ref='reference-before-send',
        request='Review password verification for vulnerabilities.',
        source='def verify(a, b): return a[:4] == b[:4]',
        facts={'code_available': True, 'paid_allowed': True, 'security_sensitive': None})
    store.append_evaluation(cdsn, capture.observation_id, kind='reference_label',
        actor_id='test-blind-annotator', reference_route='paid', acceptable_routes=['paid'])
    fake = FakeOllama(reply_json(route='paid'))
    with ShadowRunner(cdsn, config, fake.classifier()) as runner:
        assert runner.process_one().state == 'completed'
    with psycopg.connect(cdsn) as conn:
        labelled_at, sent_at = conn.execute('SELECT e.recorded_at,o.dispatched_at '
            'FROM classifier_evaluations e JOIN classifier_observations o USING(observation_id) '
            'WHERE o.observation_id=%s', (capture.observation_id,)).fetchone()
    assert labelled_at <= sent_at and fake.chat_calls == 1


def test_prior_and_current_guided_rows_keep_distinct_gates_and_provenance(cdsn, cfg):
    from bridge.classifier import scenarios as sc
    before = table_counts(cdsn)
    ids = []
    for profile in (sc.frozen_v2.GUIDED_PROFILE_ID, sc.frozen_v3.GUIDED_PROFILE_ID, sc.GUIDED_PROFILE_ID):
        config = ClassifierShadowConfig(enabled=True, scenario_guidance=True,
            profile=GenerationProfile(profile_id=profile))
        obs = ingest.capture_text(cdsn, config, origin_kind='replay', origin_ref='mixed-guided',
            request='Fix this function.', source='def f(): return 0',
            facts={'general_available': True, 'code_available': False, 'paid_allowed': False,
                   'tools_available': True, 'context_complete': True, 'security_sensitive': False})
        ids.append(obs.observation_id)
    fake = FakeOllama(reply_json(route='general_local', category='code'))
    with ShadowRunner(cdsn, cfg, fake.classifier()) as runner:
        assert runner.process_one().state == runner.process_one().state == runner.process_one().state == 'completed'
    with psycopg.connect(cdsn, row_factory=dict_row) as conn:
        rows = {r['observation_id']: r for r in conn.execute('SELECT * FROM classifier_observations')}
    old, interim, new = [rows[i] for i in ids]
    assert old['policy_allowed'] is True and new['policy_allowed'] is False
    assert interim['policy_allowed'] is False
    assert interim['policy_version'] == sc.frozen_v3.GUIDED_POLICY_VERSION
    assert interim['backend']['scenario']['catalogue_sha256'] == sc.frozen_v3.catalogue_sha256()
    assert old['policy_version'] == sc.frozen_v2.GUIDED_POLICY_VERSION
    assert new['policy_version'] == sc.GUIDED_POLICY_VERSION
    assert old['backend']['scenario']['catalogue_sha256'] == sc.frozen_v2.catalogue_sha256()
    assert new['backend']['scenario']['catalogue_sha256'] == sc.catalogue_sha256()
    assert old['rec_route'] == new['rec_route'] == 'general_local'
    assert fake.chat_bodies[0]['messages'][0]['content'] == sc.frozen_v2.GUIDED_RUBRIC
    assert fake.chat_bodies[1]['messages'][0]['content'] == sc.frozen_v3.GUIDED_RUBRIC
    assert fake.chat_bodies[2]['messages'][0]['content'] == sc.GUIDED_RUBRIC
    assert table_counts(cdsn) == before
