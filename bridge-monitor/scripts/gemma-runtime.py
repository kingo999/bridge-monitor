"""Start, inspect or stop the project-local Gemma classifier server (RX 7900 XT).

    python scripts/gemma-runtime.py start [--warm] [--config FILE]
    python scripts/gemma-runtime.py status [--probe]
    python scripts/gemma-runtime.py stop

Exit codes: 0 ready, 1 problem, 2 up but GPU not demonstrated, 3 stopped,
4 port held by an unowned process, 5 refused (identity mismatch).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.classifier.config import load_config  # noqa: E402
from bridge.classifier.lifecycle import RuntimeController  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["start", "status", "stop"])
    parser.add_argument("--config", help="TOML with a [gemma_runtime] table")
    parser.add_argument("--warm", action="store_true", help="start: load the model after launch")
    parser.add_argument("--probe", action="store_true",
                        help="status: load the model so GPU residency can be demonstrated")
    args = parser.parse_args(argv)
    controller = RuntimeController(load_config(args.config).gemma_runtime)
    if args.action == "start":
        report = controller.start(warm=args.warm)
    elif args.action == "status":
        report = controller.status(probe=args.probe)
    else:
        report = controller.stop()
    print(json.dumps(report, indent=2, sort_keys=True))
    return int(report["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
