"""Windows task entry point. Credentials remain in the existing protected file."""
import argparse
import hashlib
import json
import logging
import os
from logging.handlers import RotatingFileHandler
import sys
import threading
import tomllib
from pathlib import Path
from psycopg.conninfo import make_conninfo

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.classifier.config import ClassifierConfig
from bridge.classifier.supervisor import SafetyHalt, atomic_json, serve, validate_dsn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--config-sha256', required=True)
    parser.add_argument('--secrets', required=True)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    state = PROJECT / 'var/run/classifier-shadow-auto'
    logs = PROJECT / 'var/logs/gemma-classifier'
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / 'auto-service.log', maxBytes=1048576, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger = logging.getLogger('classifier-auto')
    logger.addHandler(handler); logger.setLevel(logging.INFO)
    try:
        paths = [Path(value).resolve() for value in (args.config, args.secrets)]
        if not all(p.is_relative_to(PROJECT) for p in paths):
            raise SafetyHalt('path_outside_project')
        config, secrets = paths
        raw_config = config.read_bytes()
        if hashlib.sha256(raw_config).hexdigest() != args.config_sha256:
            raise SafetyHalt('config_hash_changed')
        cfg = ClassifierConfig.model_validate(tomllib.loads(raw_config.decode('utf-8')))
        if not cfg.classifier_shadow.enabled:
            return 0
        if (not cfg.classifier_shadow.halt_on_cpu_fallback
                or cfg.classifier_shadow.endpoint != cfg.gemma_runtime.endpoint
                or cfg.classifier_shadow.model_digest != cfg.gemma_runtime.manifest_sha256):
            raise SafetyHalt('unsafe_classifier_configuration')
        dsn = json.loads(secrets.read_text())['dsn']
        validate_dsn(dsn, args.database)
        dsn = make_conninfo(dsn, application_name=f'gemma-classifier-auto-{os.getpid()}')
        return serve(cfg, dsn, state, threading.Event())
    except SafetyHalt as exc:
        atomic_json(state / 'halt.json', {'reason': str(exc)})
        logger.error('startup_safety_halt reason=%s', str(exc))
        return 0
    except Exception as exc:
        logger.error('startup_failed type=%s', type(exc).__name__)
        return 1
    finally:
        handler.close()


if __name__ == '__main__':
    raise SystemExit(main())
