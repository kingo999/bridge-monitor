import importlib.util
import os
import sys
from pathlib import Path

import pytest


def builder():
    path=Path(__file__).parents[1]/'scripts/outbound-audit/build-evidence.py'
    spec=importlib.util.spec_from_file_location('audit_build',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_controlled_environment_excludes_inherited_injection(monkeypatch,tmp_path):
    for key in ['CL','_CL_','LINK','_LINK_','INCLUDE','LIB','LIBPATH','PATH','PYTHONPATH']:
        monkeypatch.setenv(key,'untrusted inherited value')
    environment=builder().controlled_environment(tmp_path)
    assert all(k not in environment for k in ['CL','_CL_','LINK','_LINK_','LIBPATH','PYTHONPATH'])
    assert 'untrusted inherited value' not in environment.values()
    assert environment['TEMP']==str(tmp_path/'tmp')
    assert environment['PATH'].endswith(r'C:\Windows\System32')


def test_source_binding_uses_only_explicit_line_ending_normalization():
    build=builder()
    assert build.source_equivalent(b'line\r\n',b'line\n')
    assert not build.source_equivalent(b'line\nextra\n',b'line\n')


@pytest.mark.skipif(os.name!='nt',reason='Windows coordinator')
def test_reader_pin_mismatch_aborts_before_uac(tmp_path,monkeypatch):
    source=Path(__file__).parents[1]/'scripts/outbound-audit/runtime-verification.py'
    spec=importlib.util.spec_from_file_location('runtime_audit_test',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'PROJECT',tmp_path)
    (tmp_path/'var').mkdir();build=tmp_path/'build';(build/'first').mkdir(parents=True)
    (build/'first/bridge-outbound-reader.exe').write_bytes(b'invalid candidate')
    monkeypatch.setattr(module.c.windll.shell32,'IsUserAnAdmin',lambda:False)
    def forbidden(*args,**kwargs):raise AssertionError('UAC/native API reached despite failed pin')
    monkeypatch.setattr(module.c,'WinDLL',forbidden)
    monkeypatch.setattr(sys,'argv',['runtime-verification.py','--build',str(build/'manifest.json'),
        '--artifacts',str(tmp_path/'var/blocked'),'--expect-reader-sha256','0'*64,'--expect-bootstrap-sha256','0'*64])
    with pytest.raises(ValueError,match='pin mismatch before UAC'):
        module.main()
    assert not list((tmp_path/'var/blocked').iterdir())
