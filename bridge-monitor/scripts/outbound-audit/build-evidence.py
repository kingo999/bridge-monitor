"""Build twice without running the ETW reader; retain source, tool and output evidence."""
import argparse
import ctypes
import hashlib
import json
import os
import struct
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def imports(path):
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 60)[0]
    count = struct.unpack_from('<H', data, pe+6)[0]
    optional_size = struct.unpack_from('<H', data, pe+20)[0]
    optional = pe+24
    assert struct.unpack_from('<H', data, optional)[0] == 0x20b, 'x64 PE required'
    rva = struct.unpack_from('<I', data, optional+120)[0]
    sections = [struct.unpack_from('<IIII', data, optional+optional_size+i*40+8) for i in range(count)]
    def offset(value):
        for virtual_size, virtual, raw_size, raw in sections:
            if virtual <= value < virtual+max(virtual_size, raw_size):
                return raw+value-virtual
        raise ValueError('unmapped PE address')
    cursor = offset(rva)
    names = []
    while any(data[cursor:cursor+20]):
        name = offset(struct.unpack_from('<I', data, cursor+12)[0])
        names.append(data[name:data.index(0, name)].decode('ascii'))
        cursor += 20
    return sorted(names, key=str.lower)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'nt' or ctypes.windll.shell32.IsUserAnAdmin():
        parser.error('build under an ordinary Windows user token')
    project = Path(__file__).resolve().parents[2]
    output = args.output.resolve()
    output.relative_to(project/'var')
    output.mkdir(exist_ok=False)
    tools = Path(r'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools')
    developer = tools/'Common7/Tools/VsDevCmd.bat'
    compiler = tools/'VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe'
    linker = compiler.with_name('link.exe')
    assert compiler.is_file() and linker.is_file() and developer.is_file(), 'recorded toolchain missing'
    source = project/'scripts/outbound-audit/collector.cpp'
    before = digest(source)
    snapshot = output/'collector.cpp'
    snapshot.write_bytes(source.read_bytes())
    assert digest(snapshot) == before
    environment = os.environ.copy()
    environment['PSModulePath'] = r'C:\Windows\System32\WindowsPowerShell\v1.0\Modules'
    compiler_info = subprocess.check_output([
        r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', '-NoProfile', '-NonInteractive',
        '-Command', '$paths=@('+','.join("'"+str(p).replace("'", "''")+"'" for p in [compiler, linker])+');'
        '$paths|ForEach-Object{$s=Get-AuthenticodeSignature -LiteralPath $_;'
        '@{path=$_;signature=[string]$s.Status;version=(Get-Item -LiteralPath $_).VersionInfo.FileVersion}}|ConvertTo-Json'
    ], text=True, env=environment)
    tool_evidence = json.loads(compiler_info)
    assert all(t['signature'] == 'Valid' for t in tool_evidence), 'compiler/linker signature invalid'
    builds = []
    for name in ['first', 'second']:
        directory = output/name
        directory.mkdir()
        exe = directory/'bridge-outbound-reader.exe'
        command = (f'"{compiler}" /nologo /Bv /EHsc /W4 /std:c++17 /O2 /MT /Brepro '
                   f'"{snapshot}" /Fo"{directory / "collector.obj"}" /Fe"{exe}" /link /Brepro /INCREMENTAL:NO')
        runner = directory/'build.cmd'
        runner.write_text('@echo off\ncall "'+str(developer)+'" -no_logo -arch=x64 -host_arch=x64\n'
                          'if errorlevel 1 exit /b %errorlevel%\n'+command+'\nexit /b %errorlevel%\n')
        result = subprocess.run([r'C:\Windows\System32\cmd.exe', '/d', '/c', str(runner)],
                                capture_output=True, text=True, cwd=directory)
        log = directory/'build.log'
        log.write_text(result.stdout+result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'build failed; see {log}')
        builds.append(dict(directory=str(directory), command=command, returncode=result.returncode,
                           log_sha256=digest(log), executable_sha256=digest(exe)))
    assert builds[0]['executable_sha256'] == builds[1]['executable_sha256'], 'builds differ'
    assert digest(source) == before == digest(snapshot), 'source changed during build'
    dlls = imports(output/'first/bridge-outbound-reader.exe')
    assert {n.lower() for n in dlls} == {'advapi32.dll','tdh.dll','ws2_32.dll','kernel32.dll'}, dlls
    git = lambda *v: subprocess.check_output(['git', *v], cwd=project, text=True).strip()
    evidence = dict(at=datetime.now(timezone.utc).isoformat(), ran_reader=False, elevated=False,
        source_path=str(source), source_snapshot=str(snapshot), source_sha256=before,
        git_head=git('rev-parse','HEAD'), source_git_blob=git('hash-object',str(source)),
        previously_reviewed_source_git_blob=git('rev-parse','783a504:bridge-monitor/scripts/outbound-audit/collector.cpp'),
        compiler_tools=[{**t, 'sha256':digest(Path(t['path']))} for t in tool_evidence],
        developer_environment_sha256=digest(developer), builds=builds, reproducible=True, native_imports=dlls,
        limits='Local reproducibility with one signed MSVC toolchain; not independent compiler or supply-chain attestation')
    assert evidence['source_git_blob'] == evidence['previously_reviewed_source_git_blob']
    (output/'build-evidence.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps({'reproducible':True,'reader_sha256':builds[0]['executable_sha256'],
                      'manifest':str(output/'build-evidence.json'),'ran_reader':False}))


if __name__ == '__main__':
    main()
