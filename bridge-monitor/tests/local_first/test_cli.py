import importlib.util
from pathlib import Path


def _script():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run-local-first-holdout.py"
    spec = importlib.util.spec_from_file_location("offline_holdout_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_claude_executable_returns_uncertain_without_leaking_error_text(monkeypatch):
    script = _script()
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("private path to absent executable")
    monkeypatch.setattr(script.subprocess, "run", missing)
    settings = script.load_settings(script.PROJECT / "config" /
                                    "local-first-holdout-20261018.toml")
    result = script.sonnet_call("question", model="sonnet", settings=settings)
    assert result.state == "uncertain"
    assert result.raw is None and result.uncertainty == "FileNotFoundError"
