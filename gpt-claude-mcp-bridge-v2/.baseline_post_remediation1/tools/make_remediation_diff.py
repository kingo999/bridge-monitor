"""Diff the current v2 candidate against the pre-remediation snapshot.

    python -B tools/make_remediation_diff.py

Writes docs/remediation.diff (unified, source files only) and docs/remediation_stat.txt
(per-file added/removed lines).  The snapshot lives in .baseline_pre_remediation/ with a
MANIFEST.json of hashes; nothing outside the isolated v2 directory is read.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / ".baseline_pre_remediation"
SKIP_PARTS = {".venv", "__pycache__", ".baseline_pre_remediation", ".attack_work", ".smoke_work", "evidence"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".in", ".json", ".jsonl", ".diff"}
EXCLUDE_PREFIXES = ("docs/v1_", "docs/remediation", "docs/telemetry_", "docs/evidence", "docs/history")


def rel_files(base: Path) -> dict[str, Path]:
    out = {}
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base)
        if p.is_file() and not (SKIP_PARTS & set(rel.parts)) and p.suffix in TEXT_SUFFIXES                 and rel.name != "MANIFEST.json":
            if not rel.as_posix().startswith(EXCLUDE_PREFIXES):
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
    before, after = rel_files(SNAP), rel_files(ROOT)
    manifest = json.loads((SNAP / "MANIFEST.json").read_text(encoding="utf-8"))
    chunks: list[str] = []
    rows: list[str] = []
    for rel in sorted(set(before) | set(after)):
        a, b = read(before.get(rel)), read(after.get(rel))
        if a == b:
            continue
        status = "added" if rel not in before else "removed" if rel not in after else "modified"
        diff = list(difflib.unified_diff(a, b, f"pre/{rel}", f"post/{rel}"))
        plus = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        minus = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        rows.append(f"{rel:<52} {status:<9} +{plus:<5} -{minus}")
        chunks.extend(diff)
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "remediation.diff").write_text("".join(chunks), encoding="utf-8")
    header = (f"pre-remediation snapshot combined sha256: {manifest['combined_sha256']}\n"
              f"files changed/added/removed: {len(rows)}\n\n")
    (ROOT / "docs" / "remediation_stat.txt").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote docs/remediation.diff ({len(chunks)} lines) and docs/remediation_stat.txt ({len(rows)} files)")


if __name__ == "__main__":
    main()
