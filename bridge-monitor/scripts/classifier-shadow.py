"""Operator commands for the advisory Gemma classifier shadow.

Everything is explicit and observational. Database commands need --dsn-env NAMING an
environment variable, so the operational database is never selected implicitly.

    replay        file-based replay of labelled cases (no database)
    capture-case  capture a pre-solution router case file as an observation
    capture-json  capture {origin_kind, origin_ref, request, source?, facts?}
    run           classify queued observations, one at a time
    queue         show observation counts
    evaluate      append an operator/reference judgment
"""

import argparse
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.classifier import ingest, store  # noqa: E402
from bridge.classifier.client import OllamaClassifier  # noqa: E402
from bridge.classifier.config import load_config  # noqa: E402
from bridge.classifier.replay import run_replay  # noqa: E402
from bridge.classifier.runner import RunnerBusy, ShadowRunner  # noqa: E402


def make_client(cfg) -> OllamaClassifier:
    shadow = cfg.classifier_shadow
    return OllamaClassifier(endpoint=shadow.endpoint, model=shadow.model,
        expected_digest=shadow.model_digest, profile=shadow.profile,
        keep_alive=shadow.keep_alive, connect_timeout=shadow.connect_timeout_seconds,
        request_timeout=shadow.request_timeout_seconds, max_response_bytes=shadow.max_response_bytes)


def dsn_from(args) -> str:
    dsn = os.environ.get(args.dsn_env or "")
    if not dsn:
        raise SystemExit("--dsn-env must name a set environment variable")
    return dsn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="TOML file (default: BRIDGE_CLASSIFIER_SHADOW_CONFIG)")
    parser.add_argument("--dsn-env", help="environment variable holding the database DSN")
    sub = parser.add_subparsers(dest="command", required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("--cases", required=True)
    replay.add_argument("--seeds", default="42,43")
    replay.add_argument("--out", required=True, help="fresh directory; must not exist")
    replay.add_argument("--label", required=True)
    case = sub.add_parser("capture-case")
    case.add_argument("--case", required=True)
    case.add_argument("--work-item-id")
    case.add_argument("--replay-id")
    capture = sub.add_parser("capture-json")
    capture.add_argument("--input", required=True)
    capture.add_argument("--replay-id")
    run = sub.add_parser("run")
    run.add_argument("--max-items", type=int, default=None)
    run.add_argument("--until-idle", action="store_true")
    sub.add_parser("queue")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--observation-id", required=True)
    evaluate.add_argument("--kind", required=True,
                          choices=["reference_label", "operator_judgment", "verification",
                                   "acceptance", "correction"])
    evaluate.add_argument("--actor", required=True)
    evaluate.add_argument("--reference-route")
    evaluate.add_argument("--acceptable-routes", default="")
    evaluate.add_argument("--judgment")
    evaluate.add_argument("--reason")
    evaluate.add_argument("--supersedes")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    shadow = cfg.classifier_shadow

    if args.command == "replay":
        cases_path = Path(args.cases)
        client = make_client(cfg)
        summary = run_replay(client, json.loads(cases_path.read_text(encoding="utf-8")),
                             [int(s) for s in args.seeds.split(",")], Path(args.out),
                             label=args.label, cases_path=cases_path)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    if not shadow.enabled:
        print(json.dumps({"result": "disabled",
                          "detail": "classifier_shadow.enabled is false; nothing was done"}))
        return 0
    dsn = dsn_from(args)
    if args.command == "capture-case":
        result = ingest.capture_router_case(dsn, shadow, args.case, work_item_id=args.work_item_id,
                                            replay_id=args.replay_id)
        print(json.dumps(result.__dict__))
    elif args.command == "capture-json":
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = ingest.capture_text(dsn, shadow, origin_kind=data["origin_kind"],
            origin_ref=data["origin_ref"], request=data["request"], source=data.get("source", ""),
            facts=data.get("facts"), facts_provenance=data.get("facts_provenance"),
            task_id=data.get("task_id"), work_item_id=data.get("work_item_id"),
            replay_id=args.replay_id)
        print(json.dumps(result.__dict__))
    elif args.command == "run":
        stop = threading.Event()
        runner = ShadowRunner(dsn, shadow, make_client(cfg))
        try:
            runner.start()
        except RunnerBusy as exc:
            print(json.dumps({"result": "busy", "detail": str(exc)}))
            return 6
        try:
            max_items = args.max_items
            counts = runner.run(stop=stop, max_items=max_items if (max_items or args.until_idle) else None)
        except KeyboardInterrupt:
            stop.set()
            counts = {"interrupted": True}
        finally:
            runner.close()
        print(json.dumps(counts))
        return 4 if "halted" in counts else 0
    elif args.command == "queue":
        import psycopg
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            rows = conn.execute("SELECT state, count(*) FROM classifier_observations "
                                "GROUP BY 1 ORDER BY 1").fetchall()
        print(json.dumps(dict(rows)))
    elif args.command == "evaluate":
        identifier = store.append_evaluation(dsn, args.observation_id, kind=args.kind,
            actor_id=args.actor, reference_route=args.reference_route,
            acceptable_routes=[r for r in args.acceptable_routes.split(",") if r] or None,
            judgment=args.judgment, reason=args.reason, supersedes_id=args.supersedes)
        print(json.dumps({"evaluation_id": identifier}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
