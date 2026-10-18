"""Validated configuration for the request-scoped Skynet agent."""

import os
import re
import tomllib
import ipaddress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentSettings:
    journal_path: Path
    token: str
    ollama_endpoint: str
    model: str
    model_digest: str
    max_buffer_bytes: int
    max_prompt_bytes: int = 1048576
    max_context_tokens: int = 16384
    max_output_tokens: int = 2048
    keep_alive: str = "10m"
    max_request_bytes: int = 1200000
    receipt_retention_days: int = 30
    output_retention_seconds: float = 600
    cleanup_seconds: float = 30
    allowed_client_ips: tuple[str, ...] = ()
    ollama_connect_seconds: float = 5
    ollama_read_seconds: float = 120
    listen_host: str = "127.0.0.1"
    listen_port: int = 8874

    def __post_init__(self):
        if not self.token or not self.token.isascii() or not self.ollama_endpoint.startswith(("http://", "https://")):
            raise ValueError("agent token and Ollama endpoint required")
        if not self.model or not re.fullmatch(r"[0-9a-f]{64}", self.model_digest):
            raise ValueError("pinned agent model and digest required")
        if (min(self.max_buffer_bytes, self.max_prompt_bytes, self.max_context_tokens,
                self.max_output_tokens, self.max_request_bytes,
                self.receipt_retention_days) <= 0 or
                min(self.ollama_connect_seconds, self.ollama_read_seconds,
                    self.output_retention_seconds, self.cleanup_seconds) <= 0):
            raise ValueError("positive agent limits required")
        if not self.keep_alive or self.max_request_bytes < self.max_prompt_bytes:
            raise ValueError("configured request and keep-alive limits required")
        if self.max_output_tokens >= self.max_context_tokens:
            raise ValueError("agent output limit must fit context")
        if not 1 <= self.listen_port <= 65535:
            raise ValueError("valid agent listen port required")
        listen_ip = ipaddress.ip_address(self.listen_host)
        if not listen_ip.is_loopback and not self.allowed_client_ips:
            raise ValueError("LAN listener requires explicit allowed client IPs")
        for address in self.allowed_client_ips:
            ipaddress.ip_address(address)

    @classmethod
    def from_toml(cls, path: str | Path) -> "AgentSettings":
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        token_ref = data.get("token_ref", "")
        if not isinstance(token_ref, str) or not token_ref.startswith("env:"):
            raise ValueError("agent token must be an environment reference")
        token = os.environ.get(token_ref[4:], "")
        return cls(journal_path=Path(data["journal_path"]), token=token,
                   ollama_endpoint=data["ollama_endpoint"], model=data["model"],
                   model_digest=data["model_digest"],
                   max_buffer_bytes=data["max_buffer_bytes"],
                   max_prompt_bytes=data["max_prompt_bytes"],
                   max_context_tokens=data["max_context_tokens"],
                   max_output_tokens=data["max_output_tokens"],
                   keep_alive=data["keep_alive"],
                   max_request_bytes=data["max_request_bytes"],
                   receipt_retention_days=data["receipt_retention_days"],
                   output_retention_seconds=data["output_retention_seconds"],
                   cleanup_seconds=data["cleanup_seconds"],
                   allowed_client_ips=tuple(data.get("allowed_client_ips", [])),
                   ollama_connect_seconds=data["ollama_connect_seconds"],
                   ollama_read_seconds=data["ollama_read_seconds"],
                   listen_host=data["listen_host"], listen_port=data["listen_port"])
