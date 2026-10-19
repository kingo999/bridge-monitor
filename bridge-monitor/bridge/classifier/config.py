"""Default-off configuration for classifier shadowing and the project-local runtime."""

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from contracts.classifier import GenerationProfile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ENV = "BRIDGE_CLASSIFIER_SHADOW_CONFIG"
MODEL_NAME = "gemma4:12b"
MANIFEST_DIGEST = "4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c"
RUNTIME_SHA256 = "76604706187dc2cccfd3eccb6bff51696e1bb7e7639038ce9e0f47455177f960"


class ConfiguredFacts(BaseModel):
    """Reviewed, static trusted facts. ``None`` records an unknown, not a guess."""

    model_config = ConfigDict(extra="forbid")
    general_available: bool | None = False
    code_worker_configured: bool | None = True
    paid_allowed: bool | None = True
    tools_available: bool | None = True


class ClassifierShadowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    endpoint: str = "http://127.0.0.1:11435"
    model: str = MODEL_NAME
    model_digest: str = MANIFEST_DIGEST
    profile: GenerationProfile = Field(default_factory=GenerationProfile)
    keep_alive: str = "30m"
    connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_response_bytes: int = Field(default=8192, ge=256, le=65536)
    max_input_bytes: int = Field(default=24576, ge=256, le=262144)
    max_queue: int = Field(default=8, ge=1, le=1000)
    max_queue_age_seconds: int = Field(default=600, ge=10)
    halt_on_cpu_fallback: bool = True
    facts: ConfiguredFacts = Field(default_factory=ConfiguredFacts)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = Field(default=11435, ge=1024, le=65535)
    runtime_dir: str = "var/runtimes/ollama-0.30.9"
    runtime_sha256: str = RUNTIME_SHA256
    models_dir: str = "var/models/gemma-7900"
    model: str = MODEL_NAME
    manifest_sha256: str = MANIFEST_DIGEST
    state_dir: str = "var/run/gemma-classifier"
    log_dir: str = "var/logs/gemma-classifier"
    context_length: int = Field(default=8192, ge=512)
    keep_alive: str = "30m"
    max_queue: int = Field(default=4, ge=1, le=64)
    startup_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    stop_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    expected_gpu_library: str = "ROCm"
    expected_gpu_compute: str = "gfx1100"
    debug_logging: bool = False

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value != "127.0.0.1":
            raise ValueError("classifier runtime binds 127.0.0.1 only")
        return value

    def path(self, name: str) -> Path:
        value = Path(getattr(self, name))
        return value if value.is_absolute() else PROJECT_ROOT / value

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"


class RouteWorkCaptureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: StrictBool = False


class ClassifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    classifier_shadow: ClassifierShadowConfig = Field(default_factory=ClassifierShadowConfig)
    gemma_runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    route_work_capture: RouteWorkCaptureConfig = Field(default_factory=RouteWorkCaptureConfig)


def load_config(path: str | os.PathLike | None = None) -> ClassifierConfig:
    """Read an explicit file, else ``BRIDGE_CLASSIFIER_SHADOW_CONFIG``, else disabled defaults."""
    chosen = path or os.environ.get(CONFIG_ENV)
    if not chosen:
        return ClassifierConfig()
    with open(chosen, "rb") as handle:
        return ClassifierConfig.model_validate(tomllib.load(handle))
