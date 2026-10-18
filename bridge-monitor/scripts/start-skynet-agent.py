"""Start the project-owned Skynet job status service."""

import argparse
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Skynet request-status agent")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)

    from skynet_agent.app import create_app
    from skynet_agent.config import AgentSettings
    import uvicorn

    settings = AgentSettings.from_toml(args.config)
    settings.journal_path.parent.mkdir(parents=True, exist_ok=True)
    uvicorn.run(create_app(settings), host=settings.listen_host,
                port=settings.listen_port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
