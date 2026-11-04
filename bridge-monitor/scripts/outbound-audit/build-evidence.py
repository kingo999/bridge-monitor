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
    result=hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):
            result.update(block)
    return result.hexdigest().upper()


TOOLS=Path(r'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\14.44.35207')
SDK=Path(r'C:\Program Files (x86)\Windows Kits\10')
SDK_VERSION='10.0.26100.0'
BASELINE='783a504dd7a3a7f753156e7de389bb9799a46f75'
GIT=Path(r'C:\Program Files\Git\cmd\git.exe')


def source_equivalent(actual,baseline):
    return actual.replace(b'\r\n',b'\n')==baseline.replace(b'\r\n',b'\n')


def controlled_environment(output):
    return dict(SystemRoot=r'C:\Windows',windir=r'C:\Windows',SystemDrive='C:',
        ComSpec=r'C:\Windows\System32\cmd.exe',
        PATH=str(TOOLS/'bin/Hostx64/x64')+';'+r'C:\Windows\System32',
        TEMP=str(output/'tmp'),TMP=str(output/'tmp'),VSLANG='1033',
        INCLUDE=';'.join(map(str,[TOOLS/'include',*[SDK/'Include'/SDK_VERSION/p for p in ['ucrt','shared','um','winrt']]])),
        LIB=';'.join(map(str,[TOOLS/'lib/x64',SDK/'Lib'/SDK_VERSION/'ucrt/x64',SDK/'Lib'/SDK_VERSION/'um/x64'])))


def require(condition,reason):
    if not condition:raise ValueError(reason)


def imports(path):
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 60)[0]
    count = struct.unpack_from('<H', data, pe+6)[0]
    optional_size = struct.unpack_from('<H', data, pe+20)[0]
    optional = pe+24
    require(struct.unpack_from('<H',data,optional)[0]==0x20b,'x64 PE required')
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
    (output/'tmp').mkdir()
    compiler = TOOLS/'bin/Hostx64/x64/cl.exe'
    linker = compiler.with_name('link.exe')
    require(compiler.is_file() and linker.is_file() and GIT.is_file(),'recorded toolchain missing')
    source = project/'scripts/outbound-audit/collector.cpp'
    before = digest(source)
    snapshot = output/'collector.cpp'
    snapshot.write_bytes(source.read_bytes())
    require(digest(snapshot)==before,'snapshot differs')
    environment=controlled_environment(output)
    signature_environment=dict(environment,PSModulePath=r'C:\Windows\System32\WindowsPowerShell\v1.0\Modules')
    tool_paths=sorted({compiler,linker,*compiler.parent.rglob('*.dll')})
    compiler_info = subprocess.check_output([
        r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', '-NoProfile', '-NonInteractive',
        '-Command', '$ErrorActionPreference="Stop";$paths=@('+','.join("'"+str(p).replace("'", "''")+"'" for p in tool_paths)+');'
        '$paths|ForEach-Object{$s=Get-AuthenticodeSignature -LiteralPath $_;'
        '@{path=$_;signature=[string]$s.Status;signer=$s.SignerCertificate.Subject;version=(Get-Item -LiteralPath $_).VersionInfo.FileVersion}}|ConvertTo-Json'
    ], text=True, env=signature_environment)
    tool_evidence = json.loads(compiler_info)
    require(all(t['signature']=='Valid' for t in tool_evidence),'compiler/support binary signature invalid')
    # Retain an input inventory, not just two outputs from the same environment.
    input_roots=[TOOLS/'include',TOOLS/'lib/x64',*[SDK/'Include'/SDK_VERSION/p for p in ['ucrt','shared','um','winrt']],
                 SDK/'Lib'/SDK_VERSION/'ucrt/x64',SDK/'Lib'/SDK_VERSION/'um/x64']
    inventory={str(p):digest(p) for directory in input_roots for p in sorted(directory.rglob('*')) if p.is_file()}
    (output/'inputs-sha256.json').write_text(json.dumps(inventory,sort_keys=True),encoding='utf-8')
    baseline=subprocess.check_output([str(GIT),'cat-file','blob',BASELINE+':bridge-monitor/scripts/outbound-audit/collector.cpp'],cwd=project,env=environment)
    require(source_equivalent(source.read_bytes(),baseline),'source differs from full reviewed baseline beyond line endings')
    builds = []
    for name in ['first', 'second']:
        directory = output/name
        directory.mkdir()
        exe = directory/'bridge-outbound-reader.exe'
        command=[str(compiler),'/nologo','/Bv','/EHsc','/W4','/std:c++17','/O2','/MT','/Brepro',str(snapshot),
                 '/Fo'+str(directory/'collector.obj'),'/Fe'+str(exe),'/link','/Brepro','/INCREMENTAL:NO']
        result=subprocess.run(command,capture_output=True,text=True,cwd=directory,env=environment)
        log = directory/'build.log'
        log.write_text(result.stdout+result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'build failed; see {log}')
        builds.append(dict(directory=str(directory), command=command, returncode=result.returncode,
                           log_sha256=digest(log), executable_sha256=digest(exe)))
    require(builds[0]['executable_sha256']==builds[1]['executable_sha256'],'builds differ')
    require(digest(source)==before==digest(snapshot),'source changed during build')
    require(all(digest(Path(p))==value for p,value in inventory.items()),'toolchain input changed during builds')
    dlls = imports(output/'first/bridge-outbound-reader.exe')
    require({n.lower() for n in dlls}=={'advapi32.dll','tdh.dll','ws2_32.dll','kernel32.dll'},'unexpected DLL imports')
    git=lambda *v:subprocess.check_output([str(GIT),*v],cwd=project,text=True,env=environment).strip()
    evidence = dict(at=datetime.now(timezone.utc).isoformat(), ran_reader=False, elevated=False,
        source_path=str(source), source_snapshot=str(snapshot), source_sha256=before,
        git_head=git('rev-parse','HEAD'), source_git_blob=git('hash-object',str(source)),
        previously_reviewed_source_git_blob=git('rev-parse',BASELINE+':bridge-monitor/scripts/outbound-audit/collector.cpp'),
        compiler_tools=[{**t, 'sha256':digest(Path(t['path']))} for t in tool_evidence],
        controlled_environment=environment,environment_policy='Explicit allowlist; no inherited compiler/linker options, no command interpreter or generated batch script',
        input_roots=list(map(str,input_roots)),input_count=len(inventory),inputs_sha256=digest(output/'inputs-sha256.json'),
        sdk_version=SDK_VERSION,baseline_full_commit=BASELINE,baseline_raw_sha256=hashlib.sha256(baseline).hexdigest().upper(),
        source_comparison='raw cat-file bytes, allowing CRLF/LF differences only',
        builds=builds,reproducible=True,native_imports=dlls,
        limits='Controlled local build; trusts installed Microsoft toolchain/SDK and the approved source. Not isolation from an actively compromised build owner.')
    require(evidence['source_git_blob']==evidence['previously_reviewed_source_git_blob'],'source Git binding mismatch')
    (output/'build-evidence.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps({'reproducible':True,'reader_sha256':builds[0]['executable_sha256'],
                      'manifest':str(output/'build-evidence.json'),'ran_reader':False}))


if __name__ == '__main__':
    main()
