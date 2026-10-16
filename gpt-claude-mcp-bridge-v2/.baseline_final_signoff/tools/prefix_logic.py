"""Pure (non-executing, non-spawning) logic shared by `prefix_check.py` and the prompt builder (T8).

Nothing here imports or runs the code under inspection: the test identities a module defines are
reconstructed from its SYNTAX TREE, and failure kinds are re-derived from the retained raw log, so a
record's own claims can be checked against evidence the builder reads for itself.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

_API_MISSING = re.compile(
    r"^(?:AttributeError: .*(?:has no attribute|object has no attribute)|ImportError|ModuleNotFoundError|"
    r"TypeError: .*(?:unexpected keyword argument|positional argument|takes \d+))", re.MULTILINE)

#: names that make a class a test case when they appear as a base (directly or through the module)
_TEST_ROOTS = frozenset({"TestCase", "WorldCase", "IsolatedAsyncioTestCase"})


def classify_errors(log: str, outcomes: dict[str, str]) -> dict[str, str]:
    """Per failing test: assertion | api_missing | behavioural_error (read from the raw log)."""
    blocks: dict[str, str] = {}
    for m in re.finditer(r"^(?:FAIL|ERROR): \S+ \((?P<id>[^)]+)\)\n-+\n(?P<body>.*?)(?=^={20,}|^-{20,}\nRan )",
                         log, re.MULTILINE | re.DOTALL):
        blocks[m.group("id")] = m.group("body")
    kinds: dict[str, str] = {}
    for test, outcome in outcomes.items():
        if outcome == "fail":
            kinds[test] = "assertion"
        elif outcome == "error":
            body = blocks.get(test, "")
            last = [ln for ln in body.strip().splitlines() if ln.strip()][-1:] or [""]
            kinds[test] = "api_missing" if _API_MISSING.search(last[0]) else "behavioural_error"
    return kinds


def expected_test_ids(source: str, module: str) -> set[str]:
    """The test ids ``unittest`` would produce for ``module``, from its syntax tree alone: every method
    named ``test*`` of a class that is (transitively, within the module) a TestCase, including methods
    inherited from in-module base classes."""
    tree = ast.parse(source)
    classes: dict[str, ast.ClassDef] = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}

    def base_names(cls: ast.ClassDef) -> list[str]:
        out = []
        for b in cls.bases:
            if isinstance(b, ast.Name):
                out.append(b.id)
            elif isinstance(b, ast.Attribute):
                out.append(b.attr)
        return out

    def is_case(name: str, seen: frozenset = frozenset()) -> bool:
        cls = classes.get(name)
        if cls is None or name in seen:
            return name in _TEST_ROOTS
        return any(b in _TEST_ROOTS or is_case(b, seen | {name}) for b in base_names(cls))

    def methods(name: str, seen: frozenset = frozenset()) -> set[str]:
        cls = classes.get(name)
        if cls is None or name in seen:
            return set()
        own = {n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name.startswith("test")}
        for b in base_names(cls):
            own |= methods(b, seen | {name})
        return own

    ids: set[str] = set()
    for name in classes:
        if is_case(name):
            ids.update(f"{module}.{name}.{m}" for m in methods(name))
    return ids


def module_file(root: Path, module: str) -> Path:
    return root / (module.replace(".", "/") + ".py")


#: the harness files an overlay must carry (those that exist in the tree), besides the module and fixtures
HARNESS_FILES = ("tests/__init__.py", "tests/helpers.py", "tests/fake_codex.py", "tests/hard_kill_helper.py")
