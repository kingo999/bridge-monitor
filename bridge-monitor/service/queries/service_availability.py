"""Cached, metadata-only reachability of configured orchestration services."""
import time
import socket
import json
import os
import subprocess
import tomllib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit

import httpx
import psycopg
from bridge.classifier.config import load_config
from contracts.policy import PolicySnapshot

_cache = {}
_lock = Lock()
CACHE_SECONDS = 15
PROBE_SECONDS = 6


def get_json(url):
    started = time.monotonic()
    try:
        with httpx.Client(timeout=1.5, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return None
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1_048_576 or time.monotonic() - started > 1.5:
                        return None
                return json.loads(body)
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        return None


def ollama_ready(endpoint, model, digest):
    if not endpoint:
        return False
    result = get_json(endpoint.rstrip("/") + "/api/tags")
    models = result.get("models") if isinstance(result, dict) else None
    if not isinstance(models, list):
        return False
    return any(isinstance(item, dict) and item.get("name") == model
               and str(item.get("digest", "")).removeprefix("sha256:") == digest.removeprefix("sha256:")
               for item in models)


def claude_runtime_authenticated():
    """Run the configured CLI's metadata-only auth command; discard identity fields."""
    try:
        config_path = os.environ.get("BRIDGE_MONITOR_RUNTIME_CONFIG") or (
            Path(__file__).resolve().parents[2] / "config/runtime-capabilities.proposal.toml")
        with open(config_path, "rb") as handle:
            executable = Path(tomllib.load(handle)["claude"]["executable"])
        if not executable.is_file():
            return False
        result = subprocess.run([str(executable), "auth", "status", "--json"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=2.5, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode != 0 or len(result.stdout) > 65536:
            return False
        value = json.loads(result.stdout)
        return isinstance(value, dict) and value.get("loggedIn") is True and value.get("apiProvider") == "firstParty"
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return False


def _model_family(model):
    match = re.match(r"^(?:claude-)?(haiku|sonnet|opus)(?:$|[-\[])", model.lower())
    return match.group(1) if match else None


def claude_model_evidence(dsn, models):
    """Positive completions clear relevant failures; unrelated failures never do."""
    blocked = {model: False for model in models}
    account_blocked = False
    with psycopg.connect(dsn, connect_timeout=2) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        db.execute("SET LOCAL statement_timeout='1s'")
        quotas = []
        if db.execute("SELECT to_regclass('cc_subscription_observations')").fetchone()[0]:
            quotas = db.execute("""
                SELECT window_id FROM (
                  SELECT DISTINCT ON (window_id) window_id,consumed_percent,window_start,resets_at,valid_until
                  FROM cc_subscription_observations WHERE provider='claude'
                    AND observed_at<=now() AND recorded_at<=now()
                  ORDER BY window_id,observed_at DESC,recorded_at DESC
                ) latest WHERE consumed_percent>=100 AND window_start<=now()
                  AND now()<least(resets_at,valid_until)
                """).fetchall()
        # Reduce history in PostgreSQL: at most one latest signal per enabled
        # model plus the account. Never transfer completion payloads/history.
        signals = db.execute("""
            WITH targets AS (
              SELECT unnest(%s::text[]) AS model UNION ALL SELECT ''
            ), evidence AS NOT MATERIALIZED (
              SELECT e.event_type,e.occurred_at,e.received_at,e.event_id,
                lower(split_part(coalesce(e.requested_model,c.requested_model),'[',1)) AS requested,
                e.payload->>'provider_error_code' AS code,
                e.payload->>'provider_http_status' AS status
              FROM events e JOIN calls c USING(call_id)
                JOIN work_items w ON w.work_item_id=c.work_item_id AND e.work_item_id=c.work_item_id
              WHERE c.execution_mode='production' AND w.origin='observed' AND e.origin='observed'
                AND e.event_type IN ('call.completed','call.failed','attempt.uncertain')
                AND e.occurred_at<=now() AND e.received_at<=now()
            )
            SELECT DISTINCT ON (t.model) t.model,e.event_type
            FROM targets t JOIN evidence e ON
              e.requested ~ '^(claude-)?(haiku|sonnet|opus)($|-)'
              AND (
                (t.model='' AND (e.event_type='call.completed' OR
                  e.code IN ('authentication_failed','billing_error') OR e.status IN ('401','402','403')))
                OR (t.model<>'' AND
                  (e.requested=lower(split_part(t.model,'[',1)) OR
                   e.requested IN (substring(t.model from '(haiku|sonnet|opus)'),
                     'claude-' || substring(t.model from '(haiku|sonnet|opus)')))
                  AND (e.event_type='call.completed' OR
                    e.code IN ('rate_limit','rate_limit_error','server_error','overloaded_error') OR
                    e.status IN ('429','500','502','503','504','529')))
              )
            ORDER BY t.model,e.occurred_at DESC,e.received_at DESC,e.event_id DESC
            """, (list(models),)).fetchall()
    for target, kind in signals:
        if not target:
            account_blocked = kind != "call.completed"
        else:
            blocked[target] = kind != "call.completed"
    for window, in quotas:
        if window in {"five_hour", "seven_day"}:
            account_blocked = True
        else:
            families = set(re.split(r"[^a-z0-9]+", window.lower())) & {"opus", "sonnet", "haiku"}
            for model in models:
                if _model_family(model) in families:
                    blocked[model] = True
    return {model: not account_blocked and not blocked[model] for model in models}


def claude_recently_blocked(dsn, model=None):
    models = [model] if model else ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    return not any(claude_model_evidence(dsn, models).values())


def claude_model_availability(dsn, models):
    unavailable = {model: False for model in models}
    if not claude_runtime_authenticated():
        return unavailable
    provider = get_json("https://status.claude.com/api/v2/summary.json")
    components = provider.get("components") if isinstance(provider, dict) else None
    if not isinstance(components, list):
        return unavailable
    states = {item.get("name"): item.get("status") for item in components if isinstance(item, dict)}
    if any(states.get(name) not in {"operational", "degraded_performance"}
           for name in ("Claude Code", "Claude API (api.anthropic.com)")):
        return unavailable
    return claude_model_evidence(dsn, models)


def claude_available(dsn):
    return any(claude_model_availability(dsn, ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]).values())


def availability_policy(dsn):
    with psycopg.connect(dsn, connect_timeout=2) as db:
        db.execute("SET TRANSACTION READ ONLY")
        db.execute("SET LOCAL statement_timeout='1s'")
        row = db.execute("SELECT c.policy FROM runtime_active_policy a JOIN config_versions c "
                         "ON c.version=a.version WHERE a.singleton=true").fetchone()
    return PolicySnapshot.model_validate(row[0]) if row else None


def _check_claude(dsn, classifier_config):
    policy = availability_policy(dsn)
    if not policy or not policy.codex_claude.enabled:
        return False
    models = [profile.model_id for profile in policy.codex_claude.profiles.values() if profile.enabled]
    available = claude_model_availability(dsn, models) if models else {}
    return {"available": any(available.values()), "models": available}


def _check_qwen(dsn, classifier_config):
    policy = availability_policy(dsn)
    if not policy:
        return False
    deployment = policy.deployment
    if not ollama_ready(deployment.qwen_endpoint, deployment.qwen_model_profile, deployment.qwen_model_digest):
        return False
    if deployment.qwen_agent_endpoint:
        target = urlsplit(deployment.qwen_agent_endpoint)
        if target.scheme not in {"http", "https"} or not target.hostname or target.username or target.password:
            return False
        with socket.create_connection((target.hostname, target.port or (443 if target.scheme == "https" else 80)), timeout=1):
            pass
    return True


def _check_gemma(dsn, classifier_config):
    config = load_config(classifier_config).classifier_shadow
    return config.enabled and ollama_ready(config.endpoint, config.model, config.model_digest)


@dataclass
class _ServiceCheck:
    running: bool = False
    started: float = 0
    updated: float = 0
    checked_at: str | None = None
    available: bool = False
    models: dict = field(default_factory=dict)


def _refresh_one(key, entry, probe, dsn, classifier_config):
    available, models = False, {}
    try:
        result = probe(dsn, classifier_config)
        if isinstance(result, dict):
            available = result.get("available") is True
            models = dict(result.get("models") or {})
        else:
            available = result is True
    except Exception:
        # A probe failure belongs only to its own service. Never log identity output.
        pass
    finished = time.monotonic()
    if finished - entry.started >= PROBE_SECONDS:
        available = False
        models = {model: False for model in models}
    with _lock:
        if _cache.get(key) is entry:
            entry.running = False
            entry.updated = finished
            entry.checked_at = datetime.now(timezone.utc).isoformat()
            entry.available = available
            entry.models = models


def service_report(dsn, classifier_config=None):
    """Return immediately. One independently bounded background refresh per service."""
    jobs = []
    services, checks, claude_models = {}, {}, {}
    now = time.monotonic()
    probes = {"claude": _check_claude, "qwen": _check_qwen, "gemma": _check_gemma}
    with _lock:
        for name, probe in probes.items():
            key = (dsn, classifier_config, name)
            entry = _cache.setdefault(key, _ServiceCheck())
            if not entry.running and (not entry.updated or now - entry.updated >= CACHE_SECONDS):
                entry.running = True
                entry.started = now
                jobs.append((key, entry, probe))
            timed_out = entry.running and now - entry.started >= PROBE_SECONDS
            fresh_enough = entry.updated and now - entry.updated <= CACHE_SECONDS + PROBE_SECONDS
            services[name] = bool(entry.available and fresh_enough and not timed_out)
            state = ("timed_out" if timed_out else "refreshing" if entry.running
                     else "available" if services[name] else "unavailable")
            checks[name] = {"state": state, "checked_at": entry.checked_at}
            if name == "claude":
                claude_models = {model: ready and fresh_enough and not timed_out for model, ready in entry.models.items()}
    for key, entry, probe in jobs:
        try:
            Thread(target=_refresh_one, args=(key, entry, probe, dsn, classifier_config), daemon=True,
                   name="availability-" + key[2]).start()
        except RuntimeError:
            with _lock:
                entry.running = False
                entry.updated = time.monotonic()
                entry.checked_at = datetime.now(timezone.utc).isoformat()
                entry.available = False
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "services": services, "checks": checks,
            "claude_models": claude_models, "refreshing": any(c["state"] == "refreshing" for c in checks.values())}
