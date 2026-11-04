"""Ordinary-token verifier guards. These tests never start a scheduled task."""
import os
import subprocess
import uuid
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows verifier')
PROJECT = Path(__file__).resolve().parents[1]
VERIFY = PROJECT/'scripts/outbound-audit/verify-protected-reader.ps1'


def verify(path):
    environment = os.environ.copy()
    environment['PSModulePath'] = r'C:\Windows\System32\WindowsPowerShell\v1.0\Modules'
    return subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
                           '-NoProfile','-NonInteractive','-File',str(VERIFY),'-ReportPath',str(path)],
                          capture_output=True,text=True,env=environment)


def test_verifier_refuses_output_outside_project_evidence(tmp_path):
    target = tmp_path/'must-not-exist.json'
    result = verify(target)
    assert result.returncode != 0 and 'beneath this project var' in result.stderr
    assert not target.exists()


def test_verifier_preserves_an_existing_report():
    directory = PROJECT/'var'
    directory.mkdir(exist_ok=True)
    target = directory/('audit-verifier-test-'+uuid.uuid4().hex+'.json')
    try:
        target.write_bytes(b'original evidence')
        result = verify(target)
        assert result.returncode != 0 and 'Report already exists' in result.stderr
        assert target.read_bytes() == b'original evidence'
    finally:
        target.unlink(missing_ok=True)
