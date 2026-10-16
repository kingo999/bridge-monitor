"""Write docs/v1_v2.diff (unified diff of the v1 files vs their v2 counterparts) and
docs/v2_inventory.txt (every v2 source file with line counts).  Reads v1 only.

    .venv\\Scripts\\python.exe -B tools/make_v1_diff.py
"""

from __future__ import annotations

import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V1 = Path(r"C:\AI\gpt-claude-mcp-bridge")
COMMON = ["architect_bridge.py", "README.md"]
SKIP_DIRS = {".venv", "__pycache__", ".git", ".attack_work", ".smoke_work"}


def main() -> None:
    chunks = []
    for name in COMMON:
        a = (V1 / name).read_text(encoding="utf-8").splitlines(keepends=True)
        b = (ROOT / name).read_text(encoding="utf-8").splitlines(keepends=True)
        chunks.extend(difflib.unified_diff(a, b, f"v1/{name}", f"v2/{name}"))
    (ROOT / "docs" / "v1_v2.diff").write_text("".join(chunks), encoding="utf-8")

    rows = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if (not path.is_file() or SKIP_DIRS & set(rel.parts) or path.suffix in {".pyc"}
                or any(part.startswith(".baseline_") for part in rel.parts)):
            continue
        if rel.as_posix().startswith("docs/v1_baseline"):
            continue
        try:
            lines = len(path.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            lines = -1
        status = "changed vs v1" if rel.as_posix() in COMMON else "new"
        rows.append(f"{rel.as_posix():<48} {lines:>6} lines  {status}")
    (ROOT / "docs" / "v2_inventory.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote docs/v1_v2.diff ({len(chunks)} diff lines) and docs/v2_inventory.txt ({len(rows)} files)")


if __name__ == "__main__":
    main()
