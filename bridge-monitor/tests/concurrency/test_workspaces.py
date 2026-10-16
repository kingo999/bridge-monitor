import pytest

from bridge.runtime.workspaces import (isolated_workspace_target,
                                       fingerprint_source, materialize_source)


def test_isolated_workspace_targets_cannot_cross_task_boundary(tmp_path):
    base = tmp_path / "workspaces"
    allowed = isolated_workspace_target(base, "call-a", "src/main.py")
    other = isolated_workspace_target(base, "call-b", "src/main.py")
    assert allowed.parent != other.parent
    assert allowed.is_relative_to(base)
    with pytest.raises(ValueError, match="workspace boundary"):
        isolated_workspace_target(base, "call-a", "../other-task/secret.py")
    with pytest.raises(ValueError, match="workspace boundary"):
        isolated_workspace_target(base, "call-a", str(other))


def test_symlink_out_of_workspace_is_rejected(tmp_path):
    base = tmp_path / "workspaces"
    target = isolated_workspace_target(base, "call-a", "src/main.py")
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = target.parent / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("test host cannot create symlinks")
    with pytest.raises(ValueError, match="workspace boundary"):
        isolated_workspace_target(base, "call-a", "src/link/secret.py")


def test_source_is_copied_to_per_call_workspace_at_verified_snapshot(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("value = 1\n", encoding="utf-8")
    expected = fingerprint_source(source, max_bytes=1024)
    first = materialize_source(source, tmp_path / "workspaces", "call-a", expected,
                               max_bytes=1024)
    second = materialize_source(source, tmp_path / "workspaces", "call-b", expected,
                                max_bytes=1024)
    assert first != second
    assert (first / "main.py").read_text(encoding="utf-8") == "value = 1\n"
    (first / "main.py").write_text("value = 2\n", encoding="utf-8")
    assert (source / "main.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (second / "main.py").read_text(encoding="utf-8") == "value = 1\n"


def test_source_change_after_admission_is_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "main.py"
    path.write_text("before", encoding="utf-8")
    expected = fingerprint_source(source, max_bytes=1024)
    path.write_text("after", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot"):
        materialize_source(source, tmp_path / "workspaces", "call-a", expected,
                           max_bytes=1024)
