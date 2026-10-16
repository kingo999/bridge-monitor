"""Pilot constants and bounded independent configuration."""
from __future__ import annotations
import ipaddress
import json
import math
import re
from pathlib import Path
from typing import Mapping
from pydantic import Field, field_validator
from .models import Endpoint, Versioned, WorkerError

MODEL = 'qwen2.5-coder:14b-instruct-q4_K_M'
DIGEST = '9ec8897f747e246e970bc5cfdda85d22f1123dc2e3d34978a010a75968716849'
HOSTNAME = 'skynet.local'
INITIAL_IP = '192.168.0.78'
PORT = 11434
CONTEXT_TOKENS = 16384
OUTPUT_TOKENS = 2048
INPUT_BYTE_BUDGET = CONTEXT_TOKENS - OUTPUT_TOKENS
PROFILE = dict(model=MODEL, stream=False, keep_alive='10m', options=dict(num_ctx=CONTEXT_TOKENS, num_predict=OUTPUT_TOKENS, temperature=0))
RECOVERY = "LOCAL-00 unavailable. Enter Skynet's hostname or LAN IP, retry discovery, or continue without the local worker."
CONTINUE = 'Continuing with GPT/Claude; LOCAL-00 is unavailable for this session. You can reconnect later.'
CHOICES = ['Enter address', 'Retry discovery', 'Continue without LOCAL-00']


def valid_peer(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        if getattr(ip, 'ipv4_mapped', None):
            ip = ip.ipv4_mapped
        return not (ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_link_local)
    except ValueError:
        return False


def parse_address(value: str) -> Endpoint:
    value = value.strip()
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
        if ':' not in value:
            raise WorkerError('invalid_address')
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if not value or len(value)>253 or any(ch in value for ch in '/:@?#%[]') or re.fullmatch(r'[0-9.]+',value):
            raise WorkerError('invalid_address') from None
        labels = value.split('.')
        if any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part) for part in labels):
            raise WorkerError('invalid_address')
        if value.lower() == 'localhost':
            raise WorkerError('invalid_address')
        host = value.lower()
    else:
        if not valid_peer(str(address)):
            raise WorkerError('invalid_address')
        host = str(address)
    return Endpoint(host=host, base_url=f'http://[{host}]:{PORT}' if ':' in host else f'http://{host}:{PORT}')


class WorkerSettings(Versioned):
    preferred_hostname: str = HOSTNAME
    initial_ip: str | None = INITIAL_IP
    last_validated_ip: str | None = None
    validated_at: str | None = None
    provenance: str = 'supplied_candidate'
    discovery_timeout_s: float = Field(default=5.0, ge=1, le=15)
    generation_timeout_s: float = Field(default=180.0, ge=30, le=900)

    @field_validator('discovery_timeout_s','generation_timeout_s', mode='before')
    @classmethod
    def finite(cls,value):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('finite timeout required')
        return float(value)

    @field_validator('preferred_hostname')
    @classmethod
    def fixed_name(cls,value):
        if value != HOSTNAME:
            raise ValueError('preferred hostname is fixed')
        return value

    @field_validator('initial_ip','last_validated_ip')
    @classmethod
    def ip(cls,value):
        if value is not None:
            endpoint=parse_address(value)
            ipaddress.ip_address(endpoint.host)
            return endpoint.host
        return value


def worker_root(env: Mapping[str,str]) -> Path:
    return Path(env.get('USERPROFILE') or str(Path.home())) / '.gpt-architect-bridge-local00'


def load_worker_settings(root: Path) -> WorkerSettings:
    path=root/'config.json'
    try:
        if not path.exists():
            return WorkerSettings()
        if path.is_symlink() or not path.is_file() or path.stat().st_size>16384:
            raise ValueError('invalid config file')
        return WorkerSettings.model_validate_json(path.read_bytes())
    except (ValueError,OSError) as exc:
        raise WorkerError('worker_config_invalid') from exc
