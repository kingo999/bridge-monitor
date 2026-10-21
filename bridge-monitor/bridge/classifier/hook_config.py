"""Startup-only opt-in and an immutable configuration snapshot for the observer."""

import hashlib
import os
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bridge.classifier.config import (CONFIG_ENV, MANIFEST_DIGEST, MODEL_NAME,
                                     ClassifierConfig, ClassifierShadowConfig)
from bridge.classifier.scenarios import GUIDED_PROFILE_IDS
from contracts.classifier import GenerationProfile


def _profile_allowed(shadow: ClassifierShadowConfig) -> bool:
    if shadow.profile == GenerationProfile():
        return True
    return bool(shadow.scenario_guidance
                and any(shadow.profile == GenerationProfile(profile_id=p) for p in GUIDED_PROFILE_IDS))


@dataclass(frozen=True, slots=True)
class HookSettings:
    config_json: str
    config_sha256: str
    loaded_at: datetime
    paid_allowed: bool | None
    tools_available: bool | None
    normal_traffic_enabled: bool = False

    def shadow_config(self) -> ClassifierShadowConfig:
        return ClassifierShadowConfig.model_validate_json(self.config_json)


def load_hook_settings(*, project_root: Path, config_path: Path | None = None) -> HookSettings | None:
    chosen = config_path or os.environ.get(CONFIG_ENV)
    if not chosen:
        return None
    path = Path(chosen)
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    if not path.is_relative_to(project_root.resolve()):
        raise ValueError('hook configuration must be inside project')
    raw = path.read_bytes()
    data = tomllib.loads(raw.decode('utf-8'))
    cfg = ClassifierConfig.model_validate(data)
    # Pydantic's legacy manual switch accepts coercion; automatic opt-in does not.
    if data.get('classifier_shadow', {}).get('enabled') is not True or not cfg.route_work_capture.enabled:
        return None
    shadow = cfg.classifier_shadow
    if (shadow.model != MODEL_NAME or shadow.model_digest != MANIFEST_DIGEST
            or not _profile_allowed(shadow)
            or shadow.endpoint != 'http://127.0.0.1:11435'):
        raise ValueError('hook requires the reviewed model, profile and loopback endpoint')
    facts = data.get('classifier_shadow', {}).get('facts', {})
    for key in ('paid_allowed', 'tools_available'):
        if key in facts and type(facts[key]) is not bool:
            raise ValueError('hook fact assertions must be booleans')
    return HookSettings(shadow.model_dump_json(), hashlib.sha256(raw).hexdigest(),
                        datetime.now(timezone.utc), facts.get('paid_allowed'), facts.get('tools_available'),
                        cfg.route_work_capture.normal_traffic_enabled)
