"""Create a source-only Skynet agent archive under the project var directory."""

import hashlib
import tarfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
ARCHIVE = PROJECT / "var" / "packages" / "skynet-agent.tar.gz"
SOURCES = [
    *sorted((PROJECT / "skynet_agent").glob("*.py")),
    PROJECT / "scripts" / "start-skynet-agent.py",
    PROJECT / "requirements-skynet-agent.txt",
    PROJECT / "config" / "skynet-agent.example.toml",
]


def main() -> None:
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(ARCHIVE, "w:gz") as bundle:
        for source in SOURCES:
            bundle.add(source, arcname=source.relative_to(PROJECT).as_posix())
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    print(f"{ARCHIVE} sha256={digest}")


if __name__ == "__main__":
    main()
