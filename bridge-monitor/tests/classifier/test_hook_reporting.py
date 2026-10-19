"""Capture methods, overlap and unknown capacity cannot masquerade as route correctness."""

from bridge.classifier import ingest, store
from bridge.classifier.runner import ShadowRunner
from bridge.classifier.hook_capture import prepare_capture
from service.queries.classifier import ClassifierCohortFilter, query_classifier_cohort
from tests.classifier.fakes import FakeOllama, reply_json
from tests.classifier.test_hook_capture import settings, snapshot


def test_report_separates_capture_methods_overlap_and_unknown_code(cdsn, tmp_path):
    cfg = settings(tmp_path)
    item = snapshot(captured_at=store.utcnow())
    hook = prepare_capture(item, cfg)
    store.capture(cdsn, hook, cfg.shadow_config())
    ingest.capture_text(cdsn, cfg.shadow_config(), origin_kind='natural', origin_ref='manual-overlap',
        request='Manual input', work_item_id=item.work_item_id,
        facts=dict(general_available=False, code_available=False, paid_allowed=True))
    fake = FakeOllama(reply_json('code_local', 'code'))
    with ShadowRunner(cdsn, cfg.shadow_config(), fake.classifier()) as runner:
        runner.process_one()
        runner.process_one()
    report = query_classifier_cohort(cdsn, ClassifierCohortFilter())
    assert report['capture']['observations'] == {'manual': 1, 'route_work': 1}
    assert report['capture']['unique_work_items'] == 1
    assert report['capture']['overlap_work_items'] == 1
    assert report['fact_coverage']['code_available'] == {'true': 0, 'false': 1, 'unknown': 1}
    assert report['policy']['unknown_code_capacity_blocks'] == 1
    assert report['policy']['other_blocked'] == 1
    assert query_classifier_cohort(cdsn, ClassifierCohortFilter(capture_method='route_work'))['counts']['submitted'] == 1
    assert query_classifier_cohort(cdsn, ClassifierCohortFilter(capture_method='manual'))['counts']['submitted'] == 1
    assert 'Manual input' not in str(report)
