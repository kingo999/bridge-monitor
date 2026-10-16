"""Diff the current v2 candidate against a recorded snapshot.

    python -B tools/make_remediation_diff.py                     # cumulative, vs pre-remediation
    python -B tools/make_remediation_diff.py --base .baseline_post_remediation1 --name remediation2

Writes docs/<name>.diff (unified, source files only) and docs/<name>_stat.txt (per-file
added/removed lines).  Each snapshot lives in a `.baseline_*` directory with a MANIFEST.json of
hashes; nothing outside the isolated v2 directory is read.
"""

from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_PARTS = {".venv", "__pycache__", ".attack_work", ".smoke_work", "evidence"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".in", ".json", ".jsonl", ".diff"}
EXCLUDE_PREFIXES = ("docs/v1_", "docs/remediation", "docs/telemetry_", "docs/evidence", "docs/history")


def rel_files(base: Path) -> dict[str, Path]:
    out = {}
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES or rel.name == "MANIFEST.json":
            continue
        if SKIP_PARTS & set(rel.parts) or any(part.startswith(".baseline_") for part in rel.parts):
            continue
        if rel.as_posix().startswith(EXCLUDE_PREFIXES):
            continue
        out[rel.as_posix()] = p
    return out


def read(p: Path | None) -> list[str]:
    if p is None:
        return []
    try:
        return p.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=".baseline_pre_remediation", help="snapshot directory under the v2 root")
    parser.add_argument("--name", default="remediation", help="output stem under docs/")
    ns = parser.parse_args()

    snap = ROOT / ns.base
    before, after = rel_files(snap), rel_files(ROOT)
    manifest = json.loads((snap / "MANIFEST.json").read_text(encoding="utf-8"))
    chunks: list[str] = []
    rows: list[str] = []
    for rel in sorted(set(before) | set(after)):
        a, b = read(before.get(rel)), read(after.get(rel))
        if a == b:
            continue
        status = "added" if rel not in before else "removed" if rel not in after else "modified"
        diff = list(difflib.unified_diff(a, b, f"base/{rel}", f"now/{rel}"))
        plus = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        minus = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
        rows.append(f"{rel:<52} {status:<9} +{plus:<5} -{minus}")
        chunks.extend(diff)

    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    (docs / f"{ns.name}.diff").write_text("".join(chunks), encoding="utf-8")
    header = (f"base snapshot {ns.base} combined sha256: {manifest.get('combined_sha256', manifest.get('combined_sha256_all_files'))}\n"
              f"files changed/added/removed: {len(rows)}\n\n")
    (docs / f"{ns.name}_stat.txt").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote docs/{ns.name}.diff ({len(chunks)} lines) and docs/{ns.name}_stat.txt ({len(rows)} files)")


if __name__ == "__main__":
    main()
