"""Worker-side validation and canonical submission binding; never probes live capacity."""

import hashlib
import json

from bridge.classifier.hook_config import HookSettings
from contracts.classifier import ClassifierInput, ObservationCapture
from contracts.route_capture import RouteCapture

BINDING_VERSION = 'route-work-binding-v1'


def binding_hash(binding: dict) -> str:
    payload = {k: v for k, v in binding.items() if k != 'sha256'}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def prepare_capture(item: RouteCapture, settings: HookSettings) -> ObservationCapture | None:
    if not item.acceptable():
        return None
    facts = dict(general_available=False, code_available=None,
                 paid_allowed=settings.paid_allowed, tools_available=settings.tools_available,
                 tests_present=item.tests_present, context_complete=bool(item.source.strip()),
                 scope='single_file', security_sensitive=None, external_tools_required=None)
    inp = ClassifierInput(request=item.prompt, source=item.source, facts=facts)
    binding = dict(version=BINDING_VERSION, task_id=item.task_id, work_item_id=item.work_item_id,
                   decision_id=item.decision_id, paid_call_id=item.paid_call_id,
                   task_sha256=inp.request_sha256(), baseline_sha256=inp.source_sha256(),
                   verifier_ref=item.case_digest, output_contract=item.output_contract)
    binding['sha256'] = binding_hash(binding)
    provenance = dict(capture_method='route_work', route_work_binding=binding,
        case_origin_ref=item.origin_ref, config_sha256=settings.config_sha256,
        config_loaded_at=settings.loaded_at.isoformat(), captured_at=item.captured_at.isoformat(),
        facts_source='frozen_config_and_registered_case', code_available='unknown_at_arrival',
        case_registered_at=item.registered_at.isoformat())
    if len(json.dumps(provenance, ensure_ascii=False).encode('utf-8')) > 4096:
        return None
    return ObservationCapture(origin_kind=item.origin_kind,
        origin_ref=f'route_work:{item.work_item_id}:{binding["sha256"]}',
        task_id=item.task_id, work_item_id=item.work_item_id, input=inp,
        facts_provenance=provenance, max_input_bytes=min(24576, settings.shadow_config().max_input_bytes))
