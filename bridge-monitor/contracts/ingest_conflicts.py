"""Hash-bound local telemetry rejection; not a signed receipt or execution authority."""
import re

_MESSAGES = {
    'event_id_conflict': 'event ID conflict',
    'producer_sequence_conflict': 'producer sequence conflict',
    'operation_identity_conflict': 'operation identity conflict',
}
CONFLICT_CODES = frozenset(_MESSAGES)

class OperationConflict(ValueError):
    def __init__(self, code: str, event_sha256: str):
        if not isinstance(code, str) or code not in CONFLICT_CODES or not isinstance(event_sha256, str) or not re.fullmatch('[0-9a-f]{64}', event_sha256):
            raise ValueError('invalid conflict evidence')
        self.code = code
        self.event_sha256 = event_sha256
        super().__init__(_MESSAGES[code])
