"""Local administrative commands for the v2 bridge (not MCP tools).

    python bridge_admin.py status
    python bridge_admin.py new --session review-2
    python bridge_admin.py reset --yes
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bridge.admin import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
