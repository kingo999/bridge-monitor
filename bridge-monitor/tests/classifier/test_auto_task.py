"""Validate the generated Windows task without installing it or reading credentials."""
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows task XML')


def test_task_has_signin_recovery_and_no_overlapping_instances():
    project = Path(__file__).resolve().parents[2]
    installer = project / 'scripts/install-classifier-auto-task.ps1'
    assert installer.exists(), 'classifier automatic task installer is missing'
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(installer),
        '-ConfigPath', str(project / 'config/gemma-classifier.example.toml')],
        env={key: value for key, value in os.environ.items() if key.upper() != 'PSMODULEPATH'},
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    root = ET.fromstring(plan['xml'])
    ns = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    text = lambda path: root.find(path, ns).text
    assert text('t:Settings/t:MultipleInstancesPolicy') == 'IgnoreNew'
    assert text('t:Settings/t:ExecutionTimeLimit') == 'PT0S'
    assert text('t:Settings/t:RestartOnFailure/t:Interval') == 'PT1M'
    assert text('t:Settings/t:RestartOnFailure/t:Count') == '3'
    assert text('t:Triggers/t:TimeTrigger/t:Repetition/t:Interval') == 'PT5M'
    assert root.find('t:Triggers/t:TimeTrigger/t:Repetition/t:Duration', ns) is None
    assert root.find('t:Triggers/t:LogonTrigger/t:UserId', ns) is not None
    assert text('t:Principals/t:Principal/t:LogonType') == 'InteractiveToken'
    assert text('t:Principals/t:Principal/t:RunLevel') == 'LeastPrivilege'
    assert text('t:Actions/t:Exec/t:Command').endswith('pythonw.exe')
    arguments = text('t:Actions/t:Exec/t:Arguments')
    assert '--config-sha256' in arguments and '--secrets' in arguments
    assert '--database "bridge_monitor_pilot_m5_20261017"' in arguments
    assert 'password=' not in arguments.lower() and 'dsn=' not in arguments.lower()
