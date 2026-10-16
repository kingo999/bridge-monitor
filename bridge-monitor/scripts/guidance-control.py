"""Register an immutable guidance pack from project-local authoring files.

Manifest JSON fields: pack_id, version_label and items. Each item has item_id,
kind (rule/example), body_file and required. This command registers content;
the pack is usable only after a separately reviewed policy references its hash.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.guidance.registry import register_pack  # noqa: E402


def _project_file(value: str) -> Path:
    path = Path(value).resolve(strict=True)
    if not path.is_relative_to(ROOT) or not path.is_file():
        raise ValueError("guidance files must stay inside bridge-monitor")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--operator-id", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(_project_file(args.manifest).read_text(encoding="utf-8"))
        items = [{**item, "body": _project_file(item["body_file"]).read_bytes()}
                 for item in manifest["items"]]
        for item in items:
            del item["body_file"]
        dsn = os.environ.get("BRIDGE_MONITOR_DSN")
        token = os.environ.get("BRIDGE_MONITOR_OPERATOR_TOKEN")
        if not dsn or not token:
            raise ValueError("BRIDGE_MONITOR_DSN and BRIDGE_MONITOR_OPERATOR_TOKEN are required")
        receipt = register_pack(dsn, pack_id=manifest["pack_id"],
            version_label=manifest["version_label"], items=items,
            actor_id=args.operator_id, auth_token=token)
        print(json.dumps(receipt.__dict__, sort_keys=True))
        return 0
    except (KeyError, OSError, ValueError, PermissionError) as exc:
        print(f"guidance registration rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
