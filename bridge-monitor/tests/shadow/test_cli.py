import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT / "scripts" / "run-code-fix-shadow.py"
SUMMARY_SCRIPT = PROJECT / "scripts" / "summarize-code-fix-cohort.py"


def test_natural_dry_run_rejects_old_retrospective_fixture(tmp_path):
    config = PROJECT / "config" / "code-fix-natural-shadow.toml"
    historical = json.loads((PROJECT / "tests" / "fixtures" /
        "code-fix-shadow-20261018.json").read_text(encoding="utf-8"))[0]
    historical["id"] = "new-looking-id"
    historical["provenance"] = {"origin_ref": "claimed-new-task",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "baseline_ref": "git:claimed", "test_ref": "git:claimed"}
    cases = tmp_path / "historical.json"
    cases.write_text(json.dumps([historical]), encoding="utf-8")
    receipt = (PROJECT / "var" / "probes" / "natural-code-fix" /
        "new-looking-id.receipt.json")
    result = subprocess.run([sys.executable, str(SCRIPT), "--settings", str(config),
        "--cases", str(cases), "--receipt", str(receipt), "--dry-run"],
        cwd=PROJECT, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "excluded retrospective" in result.stderr.lower()


def test_shadow_settings_accept_configured_full_file_prompt():
    spec = importlib.util.spec_from_file_location("shadow_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    settings = module.ShadowSettings.model_validate({
        "mode": "shadow", "cohort": "natural_shadow", "parent_model": "gpt-test",
        "prompt_prefix": "Return the complete Python module as JSON.\n",
        "target_cases": 30,
        "verifier": {"image": "python@sha256:" + "a" * 64,
            "daemon_endpoint": "npipe:////./pipe/dockerDesktopLinuxEngine"}})
    assert settings.prompt_prefix.startswith("Return the complete Python module")
    assert settings.target_cases == 30


def test_empty_natural_cohort_report_stays_no_go(tmp_path):
    output = tmp_path / "summary.json"
    result = subprocess.run([sys.executable, str(SUMMARY_SCRIPT),
        "--settings", str(PROJECT / "config" / "code-fix-natural-shadow.toml"),
        "--receipt-dir", str(tmp_path), "--output", str(output)],
        cwd=PROJECT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["eligible_cases"] == 0 and summary["remaining_cases"] == 30
    assert summary["ready_for_review"] is False
    assert summary["live_canary_authorized"] is False


def test_valid_natural_dry_run_never_dispatches_or_writes_receipt(tmp_path):
    natural = {"id": "synthetic-dry-run-only",
        "task": "Fix double so it multiplies by two.",
        "baseline_source": "def double(value): return value\n",
        "reference_source": "def double(value): return value * 2\n",
        "tests": [{"function": "double", "args": [3], "expected": 6}],
        "provenance": {"origin_ref": "synthetic-test",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "baseline_ref": "test:baseline", "test_ref": "test:vectors"}
    }
    cases = tmp_path / "case.json"
    cases.write_text(json.dumps([natural]), encoding="utf-8")
    receipt = (PROJECT / "var" / "probes" / "natural-code-fix" /
        "synthetic-dry-run-only.receipt.json")
    assert not receipt.exists()
    result = subprocess.run([sys.executable, str(SCRIPT), "--settings",
        str(PROJECT / "config" / "code-fix-natural-shadow.toml"), "--cases",
        str(cases), "--receipt", str(receipt), "--dry-run"],
        cwd=PROJECT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cohort"] == "natural_shadow"
    assert not receipt.exists()
