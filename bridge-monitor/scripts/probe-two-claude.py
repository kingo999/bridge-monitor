"""Observe two concurrent, separate read-only Sonnet bridge executions.

This project-local probe does not register a client or change the existing bridge.
It requires an already reviewed two-Claude pilot policy and two distinct roots.
"""

import argparse
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import psycopg

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.startup import build_coordinator  # noqa: E402
from contracts.queries import CohortQuery  # noqa: E402
from contracts.work import TaskPacket  # noqa: E402
from service.queries.snapshot import read_snapshot  # noqa: E402


class PairedLaunch:
    def __init__(self, adapter, barrier, dsn, observation):
        self.adapter = adapter
        self.barrier = barrier
        self.dsn = dsn
        self.observation = observation

    def run(self, lease, task, prompt, emitter):
        self.barrier.wait(timeout=25)
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute("SELECT call_id,task_id,worker_instance_id FROM runtime_jobs "
                "WHERE pool='claude' AND state='active' AND slot_held "
                "ORDER BY call_id").fetchall()
        try:
            snapshot = read_snapshot(self.dsn, CohortQuery(origin="observed"))
            visible = [item["worker_instance_id"] for item in snapshot.worker_executions]
            capacity = snapshot.capacity
        except Exception as exc:
            visible = []
            capacity = {"snapshot_error": type(exc).__name__}
        self.observation.append({"observed_at": datetime.now(timezone.utc).isoformat(),
                                 "active_workers": rows,
                                 "dashboard_worker_ids": visible,
                                 "dashboard_capacity": capacity})
        return self.adapter.run(lease, task, prompt, emitter)


def _execute(index, coordinator, barrier, observations, dsn, paid_model):
    coordinator.claude_adapter = PairedLaunch(coordinator.claude_adapter, barrier,
                                               dsn, observations)
    task = TaskPacket(task_id=str(uuid.uuid4()), work_item_id=str(uuid.uuid4()),
        category="analysis", scope=["read"], token_budget=1800, call_budget=1)
    prompt = ("This is an isolated read-only concurrency probe. Do not delegate or call "
              "any tool. Return the required completion JSON with answer set to a "
              f"two-sentence description of the number {index + 1}, local_results as [], "
              "fallback_child_call_id as null, and work_resolution as resolved.")
    started = time.monotonic()
    outcome = coordinator.delegate_claude(task, prompt, requested_model=paid_model,
                                          reserve_tokens=1200)
    return {"task_id": task.task_id, "work_item_id": task.work_item_id,
            "call_id": outcome.get("call_id"), "state": outcome.get("state"),
            "reason": outcome.get("reason"), "reported_model": outcome.get("reported_model"),
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-hash", required=True)
    parser.add_argument("--output-stem", default="m4-two-claude-live")
    args = parser.parse_args()
    if not args.output_stem or any(character not in
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in args.output_stem):
        parser.error("output stem must be a simple filename")
    dsn = os.environ.get("BRIDGE_MONITOR_DSN")
    runtime = Path(os.environ.get("BRIDGE_MONITOR_RUNTIME_CONFIG", "")).resolve()
    if not dsn or not runtime.is_file() or not runtime.is_relative_to(PROJECT):
        raise SystemExit("pilot DSN and project-local runtime configuration required")
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT a.source_hash,c.policy FROM runtime_active_policy a "
            "JOIN config_versions c ON c.version=a.version WHERE a.singleton=true").fetchone()
    if not row or row[0] != args.approved_hash or row[1]["limits"]["claude_global_active"] != 2:
        raise SystemExit("reviewed two-Claude pilot policy is not active")
    if row[1]["limits"]["claude_per_root_active"] != 1:
        raise SystemExit("per-root Claude limit must remain one")

    observations = []
    barrier = threading.Barrier(2)
    coordinators = [build_coordinator(runtime, audience="parent") for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(_execute, index, coordinators[index], barrier,
                                  observations, dsn, row[1]["fallback"]["paid_model_profile"])
                   for index in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except Exception as exc:
                outcomes.append({"state": "probe_error", "error_type": type(exc).__name__,
                                 "error": str(exc)})
    calls = [item["call_id"] for item in outcomes if item.get("call_id")]
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("""SELECT j.call_id,j.task_id,j.worker_instance_id,j.state,
            min(e.occurred_at) FILTER (WHERE e.event_type='model.reported'),
            min(e.occurred_at) FILTER (WHERE e.event_type='call.completed'),
            max(e.reported_model) FILTER (WHERE e.event_type='model.reported')
            FROM runtime_jobs j LEFT JOIN events e ON e.call_id=j.call_id
            WHERE j.call_id=ANY(%s) GROUP BY j.call_id,j.task_id,j.worker_instance_id,j.state
            ORDER BY j.call_id""", (calls,)).fetchall()
        held_qwen = conn.execute("SELECT count(*) FROM runtime_jobs WHERE pool='qwen' "
                                 "AND state='fallback' AND slot_held").fetchone()[0]
    observed = [{"call_id": row[0], "task_id": row[1], "worker_instance_id": row[2],
                 "state": row[3], "model_reported_at": row[4].isoformat() if row[4] else None,
                 "completed_at": row[5].isoformat() if row[5] else None,
                 "reported_model": row[6]} for row in rows]
    intervals = [(row[4], row[5]) for row in rows]
    overlap = (len(intervals) == 2 and all(start and end for start, end in intervals)
               and max(start for start, _ in intervals) < min(end for _, end in intervals))
    active_pair = any(len({worker for _, _, worker in item["active_workers"]}) >= 2
                      and len({root for _, root, _ in item["active_workers"]}) >= 2
                      for item in observations)
    dashboard_pair = any(len(set(item["dashboard_worker_ids"])) >= 2
                         for item in observations)
    receipt = {"policy_hash": args.approved_hash, "outcomes": outcomes,
               "active_observations": observations, "recorded_executions": observed,
               "model_intervals_overlap": overlap, "independent_active_workers": active_pair,
               "dashboard_showed_two_workers": dashboard_pair,
               "held_qwen_fallback_slots_after": held_qwen}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = PROJECT / "var" / "reports" / f"{args.output_stem}-{stamp}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(target), "states": [item["state"] for item in outcomes],
                      "independent_active_workers": active_pair,
                      "dashboard_showed_two_workers": dashboard_pair,
                      "model_intervals_overlap": overlap,
                      "held_qwen_fallback_slots_after": held_qwen}, sort_keys=True))
    return 0 if (all(item["state"] == "completed" for item in outcomes) and
                 len({item["task_id"] for item in outcomes}) == 2 and
                 len({row[2] for row in rows}) == 2 and active_pair and dashboard_pair and overlap and
                 held_qwen >= 1) else 1


if __name__ == "__main__":
    raise SystemExit(main())
