"""Approved content audit, kept separate from content-free architect telemetry."""
from .models import WorkerError
from ..state import utc_now


class AuditWriter:
    def __init__(self,store):self.store=store

    def write_request(self,request_id,request,packet,prepared):
        if not packet.approval.audit_content_approved:raise WorkerError('audit_approval_required')
        path=f'audit/{request_id}/request.json'
        self.store.write(path,{'schema_version':1,'created_at':utc_now(),'request':request,
            'packet':packet.model_dump(mode='json'),'prepared':prepared.model_dump(mode='json')})
        return path

    def write_response(self,request_id,outcome):
        path=f'audit/{request_id}/response.json'
        self.store.write(path,{'schema_version':1,'received_at':utc_now(),'transport':outcome.model_dump(mode='json')})
        return path

    def write_metadata(self,request_id,metadata):
        path=f'audit/{request_id}/metadata.json'
        previous=self.store.read(path) if self.store.path(path).exists() else {}
        self.store.write(path,{**previous,'schema_version':1,'recorded_at':utc_now(),**metadata})
