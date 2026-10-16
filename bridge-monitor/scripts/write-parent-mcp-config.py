"""Write a project-local parent MCP client configuration without registering it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.launch import create_parent_mcp_config


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    executable = project / ".venv" / "Scripts" / "python.exe"
    print(create_parent_mcp_config(project, python_executable=executable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
