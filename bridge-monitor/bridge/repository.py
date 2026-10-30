"""Opt-in repository tools for a leased Bridge worker; no general shell or Git writes.

Builds/tests execute trusted repository code with the host user's privileges. These
tools enforce filesystem/command selection, not an OS sandbox for build plugins.
"""
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import tempfile
import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TOOL_NAMES = ('read_file', 'write_file', 'replace_text', 'find_files', 'search_text', 'run_checks', 'run_framework', 'read_run_log', 'git_status')
REPOSITORY_TOOLS = tuple('mcp__repository__' + name for name in TOOL_NAMES)
READ_TOOL_NAMES = ('read_file', 'find_files', 'search_text', 'read_run_log', 'git_status')
DENIED_PARTS = {'.git', '.agents', '.claude', '.codex', '.idea', '.ssh', '.aws', '.azure', '.kube', '.gcp', 'node_modules', 'target', 'pt', 'secrets'}
TEXT_SUFFIXES = {'.java', '.py', '.ts', '.tsx', '.json', '.xml', '.properties', '.md', '.txt', '.yaml', '.yml', '.toml', '.scss'}


def approved_root(value):
    """Only explicit local, existing directories, without filesystem aliases."""
    path = Path(value)
    if (not value or not path.is_absolute() or str(value).startswith(('\\\\', '//'))
            or any(part in ('.', '..') or '~' in part or part.endswith((' ', '.'))
                   for part in str(value).replace('\\', '/').split('/') if part)):
        raise PermissionError('explicit absolute local project root required')
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise PermissionError('linked repository root denied')
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise PermissionError('project root must be an existing directory')
    return resolved


def valid_prefix(prefix):
    if (not isinstance(prefix, str) or not prefix or '\\' in prefix or ':' in prefix or '//' in prefix
            or prefix.startswith('/') or any(part in ('', '.', '..') or '~' in part
            or part.endswith((' ', '.')) or any(ord(c) < 32 for c in part)
            for part in prefix.rstrip('/').split('/'))
            or not prefix.endswith('/') and not Path(prefix).suffix):
        raise ValueError('prefix must be a safe directory ending in / or an exact text filename')
    return prefix


def within_prefix(relative, prefix):
    return (relative == prefix.rstrip('/') or prefix.endswith('/') and relative.startswith(prefix))


def tool_names_for_policy(policy):
    if policy.access == 'read-only':
        return READ_TOOL_NAMES
    return TOOL_NAMES


class _ProcessJob:
    """Closing the Windows job also kills descendants if the tool server exits."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes as c
        from ctypes import wintypes as w
        class Basic(c.Structure):
            _fields_=[('process_time',c.c_int64),('job_time',c.c_int64),('flags',w.DWORD),
                      ('min_ws',c.c_size_t),('max_ws',c.c_size_t),('active',w.DWORD),
                      ('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(c.Structure):
            _fields_=[(name,c.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(c.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',c.c_size_t),('job_memory',c.c_size_t),
                      ('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
        kernel=c.WinDLL('kernel32',use_last_error=True)
        kernel.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR]
        kernel.CreateJobObjectW.restype=w.HANDLE
        kernel.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
        kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        kernel.CloseHandle.argtypes=[w.HANDLE]
        handle=kernel.CreateJobObjectW(None,None)
        info=Extended();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not handle or not kernel.SetInformationJobObject(handle,9,c.byref(info),c.sizeof(info)) or not kernel.AssignProcessToJobObject(handle,w.HANDLE(int(process._handle))):
            if handle:kernel.CloseHandle(handle)
            raise PermissionError('cannot establish build process job boundary')
        self.handle=handle;self.kernel=kernel
        self.kernel.TerminateJobObject.argtypes=[w.HANDLE,w.UINT]
        self.kernel.QueryInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.c_void_p]

    def resume(self, process):
        if os.name=='nt':
            import ctypes as c
            resume=c.WinDLL('ntdll').NtResumeProcess
            resume.argtypes=[c.c_void_p];resume.restype=c.c_long
            if resume(int(process._handle)) != 0:
                raise PermissionError('cannot resume contained build process')

    def terminate(self):
        if not self.handle:return True
        import ctypes as c
        from ctypes import wintypes as w
        class Accounting(c.Structure):
            _fields_=[('user',c.c_int64),('kernel',c.c_int64),('period_user',c.c_int64),('period_kernel',c.c_int64),
                      ('faults',w.DWORD),('total',w.DWORD),('active',w.DWORD),('terminated',w.DWORD)]
        self.kernel.TerminateJobObject(self.handle,1)
        until=time.monotonic()+10
        while time.monotonic()<until:
            info=Accounting()
            if self.kernel.QueryInformationJobObject(self.handle,1,c.byref(info),c.sizeof(info),None) and info.active==0:
                return True
            time.sleep(.05)
        return False

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle=None


def build_environment(java_home=''):
    allowed={'PATH','PATHEXT','SYSTEMROOT','WINDIR','COMSPEC','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA',
             'PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE',
             'JAVA_HOME','MAVEN_HOME','M2_HOME','MAVEN_LOCAL_REPO','MAVEN_ENV','PNPM_HOME'}
    env={k:v for k,v in os.environ.items() if k.upper() in allowed or k.upper().startswith('ARTIFACTORY_')}
    if java_home:
        env['JAVA_HOME']=java_home
        env['PATH']=str(Path(java_home)/'bin')+os.pathsep+env.get('PATH','')
    return env


class RepositoryPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    access: Literal['read-only', 'read-write'] = 'read-only'
    roots: dict[str, str] = Field(default_factory=dict)
    denied_prefixes: dict[str, list[str]] = Field(default_factory=dict)
    check_kinds: dict[str, str] = Field(default_factory=dict)
    maven_modules: dict[str, list[str]] = Field(default_factory=dict)
    write_prefixes: dict[str, list[str]] = Field(default_factory=dict)
    sql_write_prefixes: dict[str, list[str]] = Field(default_factory=dict)
    security_ai_campaigns: dict[str, list[str]] = Field(default_factory=dict)
    maven: str = ''
    pnpm: str = ''
    ui_upload_parser_install: bool = False
    java_home: str = ''
    max_file_bytes: int = Field(default=524288, ge=1, le=2097152)
    command_timeout_seconds: int = Field(default=180, ge=1, le=500)
    max_log_bytes: int = Field(default=8 * 1024 * 1024, ge=65536, le=32 * 1024 * 1024)

    @model_validator(mode='after')
    def validate_roots(self):
        if any(not re.fullmatch(r'[a-z][a-z0-9_-]*', key) or not Path(value).is_absolute()
               for key, value in self.roots.items()):
            raise ValueError('repository roots require simple IDs and absolute paths')
        if self.enabled:
            try:
                roots = [approved_root(value) for value in self.roots.values()]
            except (OSError, PermissionError) as exc:
                raise ValueError('repository roots must be existing unlinked local directories') from exc
            if any(a.is_relative_to(b) or b.is_relative_to(a)
                   for i, a in enumerate(roots) for b in roots[i+1:]):
                raise ValueError('repository roots must not overlap or alias')
        for prefixes in (self.write_prefixes, self.sql_write_prefixes, self.denied_prefixes):
            for values in prefixes.values():
                for value in values:
                    valid_prefix(value)
        if any(not re.fullmatch(r'[A-Za-z0-9_./-]{1,150}', module)
               or '..' in module.split('/') or module.startswith(('/', '-'))
               for modules in self.maven_modules.values() for module in modules):
            raise ValueError('invalid host Maven module selector')
        return self

    def for_task(self, task):
        ids = task.context.get('repository_ids', '').split(',')
        if (not self.enabled or task.scope != ['repository-execution'] or not ids
                or len(set(ids)) != len(ids) or any(key not in self.roots for key in ids)):
            raise PermissionError('repository capability or task binding unavailable')
        access = task.context.get('repository_access')
        if access not in ('read-only', 'read-write'):
            raise PermissionError('explicit repository_access read-only or read-write required')
        selected = {key: str(approved_root(self.roots[key])) for key in ids}
        if approved_root(task.context.get('project_root', '')) not in {Path(p) for p in selected.values()}:
            raise PermissionError('project root is not a selected approved repository')
        writes = {}
        if access == 'read-write':
            try:
                requested = json.loads(task.context.get('repository_write_prefixes', 'null'))
                if not isinstance(requested, dict) or not requested or set(requested) - set(ids):
                    raise ValueError('selected-root write scopes required')
                for key, prefixes in requested.items():
                    if not isinstance(prefixes, list) or not prefixes:
                        raise ValueError('nonempty prefix lists required')
                    for prefix in prefixes:
                        valid_prefix(prefix)
                        if not any(within_prefix(prefix, host) for host in self.write_prefixes.get(key, [])):
                            raise PermissionError('task write scope exceeds host grant')
                    writes[key] = list(prefixes)
            except (TypeError, ValueError) as exc:
                raise PermissionError('explicit bounded repository_write_prefixes JSON required') from exc
        scoped = self.model_copy(deep=True, update={'roots':selected, 'access':access,
            'write_prefixes':writes,
            'sql_write_prefixes':({k:list(self.sql_write_prefixes.get(k, [])) for k in ids}
                                  if access == 'read-write' else {}),
            'security_ai_campaigns':({k:list(self.security_ai_campaigns.get(k, [])) for k in writes}
                                      if access == 'read-write' else {}),
            'check_kinds':({k:self.check_kinds[k] for k in writes if k in self.check_kinds}
                            if access == 'read-write' else {})})
        return scoped


def configure_repository_command(command, policy):
    command = list(command)
    for option in ('--allowedTools', '--max-turns'):
        if option in command:
            i = command.index(option)
            del command[i:i+2]
    allowed = ['mcp__repository__' + name for name in tool_names_for_policy(policy)]
    command.extend(['--allowedTools', ','.join(allowed), '--max-turns', '64'])
    return command


class RepositoryTools:
    def __init__(self, policy, authorize, log_root=None, deadline_epoch=None):
        self.policy = policy
        self.authorize = authorize
        self.log_root = Path(log_root) if log_root else None
        self.deadline_epoch = deadline_epoch

    def check(self):
        if not self.policy.enabled or not self.authorize():
            raise PermissionError('repository lease unavailable or revoked')

    def check_mutation(self):
        self.check()
        if self.policy.access != 'read-write':
            raise PermissionError('task repository grant is read-only')

    def path(self, repository, relative, write=False, _checked=False):
        if not _checked:self.check()
        if write:self.check_mutation()
        if repository not in self.policy.roots:
            raise PermissionError('repository not granted')
        relative = str(relative).replace('\\', '/')
        p = Path(relative)
        parts = relative.split('/')
        if (p.is_absolute() or PureWindowsPath(relative).is_absolute() or ':' in relative
                or not relative or any(part in ('', '.', '..') for part in parts)
                or any(part.endswith(('.', ' ')) or '~' in part or any(ord(c)<32 for c in part) for part in parts)
                or any(part.lower() in DENIED_PARTS for part in parts)):
            raise PermissionError('repository path denied')
        lower = relative.lower()
        if any(within_prefix(lower, prefix.lower())
               for prefix in self.policy.denied_prefixes.get(repository, [])):
            raise PermissionError('repository component excluded by host policy')
        if (any(part.lower().startswith('.env') or part.lower().startswith('env-local') for part in parts)
                or lower.endswith(('.npmrc', '.p12', '.jks', '.pem', '.der', '.key'))
                or lower == '.mvn/settings.xml'):
            raise PermissionError('secret/configuration path denied')
        root = approved_root(self.policy.roots[repository])
        current = root
        for part in parts:
            current = current / part
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise PermissionError('linked repository path denied')
            if current.is_file() and current.stat().st_nlink > 1:
                raise PermissionError('hard-linked repository file denied')
        target = current.resolve()
        if not target.is_relative_to(root):
            raise PermissionError('repository containment failed')
        if write:
            allowed = self.policy.write_prefixes.get(repository)
            if not allowed or not any(within_prefix(relative, prefix)
                                   for prefix in allowed):
                raise PermissionError('write is outside the task file scope')
            if target.name.lower() in {'pom.xml','package.json','pnpm-lock.yaml','package-lock.json'} or '.mvn' in parts:
                raise PermissionError('build descriptor mutation is not granted')
            sql_granted = (target.suffix.lower() == '.sql' and any(
                prefix.endswith('/') and relative.startswith(prefix)
                for prefix in self.policy.sql_write_prefixes.get(repository, [])))
            if target.suffix.lower() not in TEXT_SUFFIXES and not sql_granted:
                raise PermissionError('only approved text files may be written')
        return target

    def read_file(self, repository: str, path: str, start_line: int = 1, max_lines: int = 160):
        target = self.path(repository, path)
        if not 1 <= start_line or not 1 <= max_lines <= 500:
            raise ValueError('invalid line window')
        data = target.read_bytes()
        if len(data) > self.policy.max_file_bytes:
            raise ValueError('file exceeds read budget')
        lines = data.decode('utf-8-sig').splitlines()
        return {'path': path, 'sha256': hashlib.sha256(data).hexdigest(), 'total_lines': len(lines),
                'text': '\n'.join(f'{i+1}: {lines[i]}' for i in range(start_line-1, min(len(lines), start_line-1+max_lines)))}

    def write_file(self, repository: str, path: str, content: str, expected_sha256: str | None = None):
        target = self.path(repository, path, write=True)
        data = content.encode('utf-8')
        if len(data) > self.policy.max_file_bytes or '\0' in content:
            raise ValueError('file exceeds write budget or contains binary content')
        old = target.read_bytes() if target.is_file() else None
        if (old is not None and hashlib.sha256(old).hexdigest() != expected_sha256
                or old is None and expected_sha256 is not None):
            raise ValueError('file changed or expected hash missing')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.bridge-write-',delete=False) as handle:
                temporary=Path(handle.name);handle.write(data)
            self.path(repository,path,write=True)
            now=target.read_bytes() if target.is_file() else None
            if now!=old:raise ValueError('file changed during write')
            os.replace(temporary,target)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
        return {'path': path, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}

    def replace_text(self, repository: str, path: str, old_text: str, new_text: str, expected_sha256: str):
        """Replace one unique substring with a current full-file hash; accept read_file LF windows in CRLF files."""
        target = self.path(repository, path, write=True)
        data = target.read_bytes()
        if len(data) > self.policy.max_file_bytes or hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError('file changed or exceeds edit budget')
        old = old_text.encode('utf-8')
        replacement = new_text.encode('utf-8')
        # read_file displays normalized LF windows. If exact bytes are absent, allow
        # that same unique window with CRLF and retain CRLF inside its replacement.
        # No other bytes change; the full-file hash, uniqueness and path guards still apply.
        if old and data.count(old) == 0 and b'\n' in old and b'\r' not in old:
            old = old.replace(b'\n', b'\r\n')
            replacement = replacement.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        if not old or data.count(old) != 1:
            raise ValueError('replacement must match exactly once')
        updated = data.replace(old, replacement, 1).decode('utf-8')
        return self.write_file(repository, path, updated, expected_sha256)

    def find_files(self, repository: str, prefix: str = '', pattern: str = '*'):
        return self._find_files(repository, prefix, pattern, limit=500)

    def _find_files(self, repository, prefix, pattern, *, limit):
        self.check()
        if repository not in self.policy.roots or len(pattern) > 100 or '/' in pattern or '\\' in pattern:
            raise ValueError('invalid repository or filename pattern')
        root = approved_root(self.policy.roots[repository])
        base = self.path(repository, prefix, _checked=True) if prefix else root
        result = []
        visited=0
        import fnmatch
        for folder, dirs, files in os.walk(base, followlinks=False):
            visited+=len(dirs)+len(files)
            if visited>10000:return {'files':result,'truncated':True}
            dirs[:] = [name for name in dirs if name.lower() not in DENIED_PARTS
                       and not (Path(folder)/name).is_symlink()
                       and not (hasattr(Path(folder)/name, 'is_junction') and (Path(folder)/name).is_junction())]
            for name in files:
                if not fnmatch.fnmatch(name, pattern):
                    continue
                relative = (Path(folder)/name).relative_to(root).as_posix()
                try:
                    self.path(repository, relative, _checked=True)
                except PermissionError:
                    continue
                result.append(relative)
                if len(result) == limit:
                    return {'files': result, 'truncated': True}
        return {'files': result, 'truncated': False}

    def search_text(self, repository: str, text: str, prefix: str = '', pattern: str = '*.java'):
        """Bounded literal substring search (not regex); pattern is a filename glob."""
        if not text or len(text) > 200:
            raise ValueError('bounded literal search required')
        found = []
        listing = self._find_files(repository, prefix, pattern, limit=5000)
        for relative in listing['files']:
            p = self.path(repository, relative, _checked=True)
            if p.stat().st_size > self.policy.max_file_bytes:
                continue
            for number, line in enumerate(p.read_text(encoding='utf-8-sig', errors='replace').splitlines(), 1):
                if text in line:
                    found.append({'path': relative, 'line': number, 'text': line[:400]})
                    if len(found) == 100:
                        return {'matches': found, 'truncated': True}
        return {'matches': found, 'truncated': listing['truncated']}

    def build_command(self, repository, kind, goal, tests='', offline=True):
        if repository not in self.policy.roots or kind != self.policy.check_kinds.get(repository):
            raise ValueError('check kind not granted for repository')
        if tests and not re.fullmatch(r'[A-Za-z0-9_.$,*#-]{1,500}', tests):
            raise ValueError('invalid test selector')
        if kind == 'maven' and goal in ('compile', 'test', 'verify', 'version') and self.policy.maven:
            if goal == 'version':
                return [self.policy.maven, '--version']
            result = [self.policy.maven, '-B', '-ntp', '-T', '1']
            modules = self.policy.maven_modules.get(repository, ['report-application'])
            if modules:
                result.extend(['-pl', ','.join(modules), '-am'])
            if offline:
                result.append('-o')
            if tests:
                result.extend(['-Dtest='+tests, '-Dsurefire.failIfNoSpecifiedTests=false'])
            return result+[goal]
        if kind == 'ui' and self.policy.pnpm:
            base = [self.policy.pnpm, '--filter', 'mri-ui-server']
            if goal == 'test' and (not tests or re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}\.spec\.ts', tests)):
                return base+['test:auth']+([tests] if tests else [])
            if not tests and goal == 'typecheck':
                return base+['exec','tsc','--noEmit']
            if not tests and goal == 'auth-build':
                return base+['test:auth:build']
            if not tests and goal == 'prepare-upload-parser' and self.policy.ui_upload_parser_install:
                return base+['add','--save-exact','--ignore-scripts','@fastify/busboy@3.2.4']
        raise ValueError('command/goal not allowlisted')

    def run_checks(self, repository: str, kind: str, goal: str, tests: str = '', offline: bool = True):
        self.check_mutation()
        command = self.build_command(repository, kind, goal, tests, offline)
        return self._run_command(repository, command)

    def run_framework(self, repository: str, campaign: str, action: str, payload: dict | None = None):
        """Fixed, opt-in campaign operations; no arbitrary shell or raw PT access."""
        self.check_mutation()
        from bridge.security_ai_workflow import prepare_request
        root, request = prepare_request(self.policy, repository, campaign, action, payload)
        if self.log_root is None:
            raise PermissionError('run log binding required')
        self.log_root.mkdir(parents=True, exist_ok=True)
        path = self.log_root / (str(uuid.uuid4()) + '.workflow-request.json')
        with path.open('x', encoding='utf-8') as handle:
            json.dump(request, handle)
        import sys
        command = [sys.executable, '-B', str(Path(__file__).with_name('security_ai_workflow.py')),
                   '--root', str(root), '--request', str(path)]
        return self._run_command(repository, command)

    def _run_command(self, repository, command):
        self.check_mutation()
        approved_root(self.policy.roots[repository])
        if self.log_root is None:
            raise PermissionError('run log binding required')
        self.log_root.mkdir(parents=True, exist_ok=True)
        run_id = str(uuid.uuid4())
        log = self.log_root/(run_id+'.log')
        env=build_environment(self.policy.java_home)
        started = time.monotonic()
        bound = self.policy.command_timeout_seconds
        if self.deadline_epoch is not None:
            remaining=self.deadline_epoch-time.time()-15
            if remaining<1:raise PermissionError('insufficient remaining worker budget for a command')
            bound=min(bound,remaining)
        timed_out = False
        revoked = False
        log_limit = False
        with log.open('wb') as handle:
            process = subprocess.Popen(command, cwd=self.policy.roots[repository], env=env,
                stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|(0x4 if os.name=='nt' else 0), start_new_session=os.name!='nt')
            job = None
            try:
                job = _ProcessJob(process)
                job.resume(process)
                while process.poll() is None:
                    if time.monotonic()-started > bound:
                        timed_out = True
                        break
                    if log.stat().st_size > self.policy.max_log_bytes:
                        log_limit = True
                        break
                    try:
                        self.check()
                    except PermissionError:
                        revoked = True
                        break
                    time.sleep(.3)
            finally:
                if process.poll() is None:
                    if os.name == 'nt' and job is not None:
                        job.terminate()
                    elif os.name == 'nt':
                        process.kill()
                    else:
                        import signal
                        os.killpg(process.pid,signal.SIGKILL)
                    try:process.wait(timeout=10)
                    finally:
                        if job is not None:job.close()
                elif job is not None:
                    try:job.terminate()
                    finally:job.close()
        receipt = {'run_id': run_id, 'exit_code': process.returncode,
                   'timed_out': timed_out, 'revoked': revoked, 'log_limit': log_limit,
                   'terminated':process.poll() is not None,
                   'effective_timeout_seconds':round(bound,2),
                   'seconds': round(time.monotonic()-started,2)}
        (self.log_root/(run_id+'.json')).write_text(json.dumps(receipt),encoding='utf-8')
        if revoked:
            raise PermissionError('repository lease revoked; command terminated; receipt retained')
        return {**receipt,'tail':self.read_run_log(run_id)['text']}

    def read_run_log(self, run_id: str, tail_lines: int = 65):
        self.check()
        if not re.fullmatch(r'[a-f0-9-]{36}', run_id) or not 1 <= tail_lines <= 150 or self.log_root is None:
            raise ValueError('invalid run log request')
        p = self.log_root/(run_id+'.log')
        with p.open('rb') as handle:
            handle.seek(max(0,p.stat().st_size-32000))
            data=handle.read(32000).decode('utf-8',errors='replace')
        return {'text':'\n'.join(data.splitlines()[-tail_lines:])}

    def git_status(self, repository: str):
        self.check()
        if repository not in self.policy.roots:
            raise PermissionError('repository not granted')
        root=str(approved_root(self.policy.roots[repository]))
        return {name:subprocess.check_output(['git','--no-optional-locks','-c','core.fsmonitor=false','-C',root,*args],text=True,
                timeout=15,env=build_environment(),stdin=subprocess.DEVNULL).strip()[:16000]
                for name,args in [('head',['rev-parse','HEAD']),('status',['status','--porcelain=v1','--untracked-files=all']),('diff_stat',['diff','--stat'])]}
