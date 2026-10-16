"""Supervisor-reported evidence and durable single-feedback-retry eligibility."""
from .models import WorkerError,canonical_packet_hash,content_hash
from .audit import AuditWriter


def retry_feedback(task,request):
    if request.attempt==1:
        if task and any(v.verdict=='accepted' for v in task.validations):raise WorkerError('task_closed')
        return None
    if task is None or task.packet_hash!=canonical_packet_hash(request.packet):raise WorkerError('retry_not_eligible')
    if any(v.verdict=='accepted' for v in task.validations):raise WorkerError('retry_not_eligible')
    first=task.attempts.get('1')
    if not first or first.retired or not first.result or first.result.state!='draft_returned' or first.result.needs_context:
        raise WorkerError('retry_not_eligible')
    reports=[v for v in task.validations if v.attempt==1]
    if not reports:raise WorkerError('retry_not_eligible')
    failure=reports[-1]
    if failure.verdict!='failed' or not failure.diagnostics.strip() or failure.supervisor_edits or request.feedback_ref!=first.result.result_digest:
        raise WorkerError('retry_not_eligible')
    return failure


class ValidationRecorder:
    def __init__(self,store):self.store=store

    def record(self,report):
        with self.store.try_lock():
            task=self.store.read_task(report.task_id)
            attempt=task.attempts.get(str(report.attempt)) if task else None
            if not task or task.packet_hash!=report.packet_hash or not attempt or not attempt.result or attempt.result.result_digest!=report.result_digest:
                raise WorkerError('validation_identity_conflict')
            result=attempt.result
            if report in task.validations:
                # An old identical delivery is a read of the current verdict,
                # not a new correction that can replace a later report.
                return result.model_copy(update={'action':'record_validation'})
            required={check.check_id:check for check in task.packet.checks}
            observed={item.check_id:item for item in report.observations}
            if len(observed)!=len(report.observations) or not set(observed)<=set(required):raise WorkerError('validation_evidence_incomplete')
            if report.verdict=='accepted':
                if result.state!='draft_returned' or result.needs_context or attempt.retired:
                    raise WorkerError('validation_evidence_incomplete')
                if set(observed)!=set(required) or not all(item.passed for item in observed.values()):raise WorkerError('validation_evidence_incomplete')
                if task.packet.category!='supplied_code_explanation' and not report.toolchain_ref:
                    raise WorkerError('validation_evidence_incomplete')
            if report.verdict=='failed' and (not report.diagnostics.strip() or not any(not item.passed for item in observed.values())):
                raise WorkerError('validation_evidence_incomplete')
            result=result.model_copy(update={'validation':report.verdict,'evidence_origin':'supervisor_reported'})
            if report.supervisor_edits:result.code='supervisor_corrected'
            task.validations.append(report)
            task.attempts[str(report.attempt)].result=result
            self.store.save_task(task)
            self.store.write(f'audit/{attempt.request_id}/validation-{len(task.validations):04d}.json',report)
            AuditWriter(self.store).write_metadata(attempt.request_id,{'task_id':report.task_id,'attempt':report.attempt,
                'first_pass_acceptance':report.attempt==1 and report.verdict=='accepted' and not report.supervisor_edits,
                'retry_outcome':report.verdict if report.attempt==2 else None,'supervisor_edits':report.supervisor_edits,
                'validation_effort':report.effort.model_dump(mode='json'),'evidence_origin':'supervisor_reported',
                'material_errors':report.diagnostics if report.verdict!='accepted' else None,
                'escalation_reason':report.verdict if report.verdict=='unverifiable' or (report.attempt==2 and report.verdict!='accepted') else None})
            return result.model_copy(update={'action':'record_validation'})
