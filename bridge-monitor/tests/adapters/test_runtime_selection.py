import runpy
from pathlib import Path

import pytest


def test_launcher_accepts_only_existing_project_config_toml(tmp_path):
    select = runpy.run_path(str(Path(__file__).parents[2] / 'scripts/start-desktop-mcp.py'))['select_runtime']
    config = tmp_path / 'config'
    config.mkdir()
    chosen = config / 'task.local.toml'
    chosen.write_text('')
    assert select(tmp_path, str(chosen)) == chosen
    outside = tmp_path / 'private.toml'
    outside.write_text('')
    with pytest.raises(ValueError):
        select(tmp_path, str(outside))
    with pytest.raises((ValueError, OSError)):
        select(tmp_path, str(config / 'missing.toml'))
