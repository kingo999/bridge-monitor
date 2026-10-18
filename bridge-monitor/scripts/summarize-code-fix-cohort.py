"""Read-only aggregation of separately registered natural shadow receipts."""

import argparse
import json
import sys
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from bridge.local_first_runner import _save  # noqa: E402
from bridge.shadow_cohort import summarize_receipts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", type=Path,
        default=PROJECT / "config" / "code-fix-natural-shadow.toml")
    parser.add_argument("--receipt-dir", type=Path,
        default=PROJECT / "var" / "probes" / "natural-code-fix")
    parser.add_argument("--output", type=Path,
        default=PROJECT / "var" / "probes" / "natural-code-fix-summary.json")
    args = parser.parse_args(argv)
    for path in (args.settings, args.receipt_dir, args.output):
        if not path.resolve().is_relative_to(PROJECT):
            raise SystemExit("cohort files must stay under bridge-monitor")
    settings = tomllib.loads(args.settings.read_text(encoding="utf-8"))
    if settings.get("cohort") != "natural_shadow":
        raise SystemExit("natural shadow settings required")
    target = settings.get("target_cases")
    if type(target) is not int or target < 1:
        raise SystemExit("positive target_cases required")
    paths = sorted(args.receipt_dir.glob("*.receipt.json")) if args.receipt_dir.exists() else []
    summary = summarize_receipts(paths, target_cases=target)
    _save(args.output, summary)
    print(json.dumps({"summary": summary, "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
