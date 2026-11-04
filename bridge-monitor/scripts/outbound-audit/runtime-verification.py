"""Bounded ordinary-user test coordinator; production audit tasks stay disabled."""
import argparse
import base64
import ctypes as c
from ctypes import wintypes as w
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import time
import uuid

PROJECT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(PROJECT))
from bridge.audit_hooks import creation_time
from bridge.audit_cli import refresh_addresses
from bridge.audit_spool import SpoolConsumer
from bridge.outbound_audit import Journal


class Guid(c.Structure):
    _fields_=[('a',w.DWORD),('b',w.WORD),('d',w.WORD),('tail',c.c_ubyte*8)]


class Wnode(c.Structure):
    _fields_=[('BufferSize',w.DWORD),('ProviderId',w.DWORD),('HistoricalContext',c.c_ulonglong),
              ('Timestamp',c.c_longlong),('Guid',Guid),('ClientContext',w.DWORD),('Flags',w.DWORD)]


class Properties(c.Structure):
    _fields_=[('Wnode',Wnode),*[(n,w.DWORD) for n in ['BufferSize','MinimumBuffers','MaximumBuffers','MaximumFileSize',
               'LogFileMode','FlushTimer','EnableFlags','AgeLimit','NumberOfBuffers','FreeBuffers','EventsLost',
               'BuffersWritten','LogBuffersLost','RealTimeBuffersLost']],('LoggerThreadId',w.HANDLE),
               ('LogFileNameOffset',w.DWORD),('LoggerNameOffset',w.DWORD)]


def etw_probe(reader_pid,stop=False):
    api=c.WinDLL('advapi32',use_last_error=True)
    api.ControlTraceW.argtypes=[c.c_ulonglong,w.LPCWSTR,c.POINTER(Properties),w.ULONG]
    api.ControlTraceW.restype=w.ULONG
    name='BridgeOutboundAudit-'+str(int(reader_pid))
    buffer=c.create_string_buffer(4096);props=c.cast(buffer,c.POINTER(Properties));props.contents.Wnode.BufferSize=len(buffer)
    status=api.ControlTraceW(0,name,props,0)  # Query only.
    result=dict(session=name,query_status=int(status),elevated=False)
    if status==0:
        api.EventAccessQuery.argtypes=[c.POINTER(Guid),w.LPVOID,c.POINTER(w.ULONG)]
        api.EventAccessQuery.restype=w.ULONG
        size=w.ULONG();rc=api.EventAccessQuery(c.byref(props.contents.Wnode.Guid),None,c.byref(size))
        result['security_query_status']=int(rc)
        if rc in (0,234) and 0<size.value<1024*1024:
            security=c.create_string_buffer(size.value);rc=api.EventAccessQuery(c.byref(props.contents.Wnode.Guid),security,c.byref(size))
            result['security_query_status']=int(rc)
            if rc==0:
                api.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes=[w.LPVOID,w.DWORD,w.DWORD,c.POINTER(w.LPVOID),c.POINTER(w.DWORD)]
                text=w.LPVOID()
                if api.ConvertSecurityDescriptorToStringSecurityDescriptorW(security,1,7,c.byref(text),None):
                    result['guid_sddl']=c.wstring_at(text);c.WinDLL('kernel32').LocalFree(text)
        result['security_scope']='EventAccessQuery GUID permissions; Windows defaults apply when no explicit GUID entry exists'
    if stop:
        # Deliberate availability test on this exact disposable verification session.
        props.contents.Wnode.BufferSize=len(buffer)
        result['stop_status']=int(api.ControlTraceW(0,name,props,1))
    return result


def tls_request():
    sent=0
    with socket.create_connection(('www.google.com',443),timeout=8) as sock:
        remote=sock.getpeername();local=sock.getsockname()
        incoming,outgoing=ssl.MemoryBIO(),ssl.MemoryBIO()
        tls=ssl.create_default_context().wrap_bio(incoming,outgoing,server_side=False,server_hostname='www.google.com')
        def flush():
            nonlocal sent
            data=outgoing.read()
            if data:sock.sendall(data);sent+=len(data)
        def feed():
            flush();data=sock.recv(65536)
            if not data:raise RuntimeError('TLS peer closed early')
            incoming.write(data)
        while True:
            try:tls.do_handshake();flush();break
            except ssl.SSLWantReadError:feed()
        request=b'GET / HTTP/1.1\r\nHost: www.google.com\r\nConnection: close\r\n\r\n'
        tls.write(request);flush();status=bytearray()
        while len(status)<256 and not status.endswith(b'\r\n'):
            try:
                chunk=tls.read(1)
                if not chunk:raise RuntimeError('No HTTP status')
                status.extend(chunk);flush()
            except ssl.SSLWantReadError:feed()
        code=int(status.decode('ascii').split()[1])
        return dict(ip=remote[0],port=remote[1],local_port=local[1],tls_wire_bytes=sent,
                    application_bytes=len(request),http_status=code,tls_version=tls.version(),certificate_verified=True)


class ShellExecuteInfo(c.Structure):
    _fields_=[('cbSize',w.DWORD),('fMask',w.ULONG),('hwnd',w.HWND),('lpVerb',w.LPCWSTR),('lpFile',w.LPCWSTR),
              ('lpParameters',w.LPCWSTR),('lpDirectory',w.LPCWSTR),('nShow',c.c_int),('hInstApp',w.HINSTANCE),
              ('lpIDList',w.LPVOID),('lpClass',w.LPCWSTR),('hkeyClass',w.HKEY),('dwHotKey',w.DWORD),
              ('hIcon',w.HANDLE),('hProcess',w.HANDLE)]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build',required=True,type=Path)
    parser.add_argument('--artifacts',required=True,type=Path)
    parser.add_argument('--expect-reader-sha256',required=True)
    parser.add_argument('--expect-bootstrap-sha256',required=True)
    parser.add_argument('--watchdog-drill',action='store_true')
    args=parser.parse_args()
    if c.windll.shell32.IsUserAnAdmin():parser.error('Run coordinator as ordinary user')
    args.artifacts.resolve().relative_to(PROJECT/'var');args.artifacts.mkdir(exist_ok=False)
    os.environ['PSModulePath']=r'C:\Windows\System32\WindowsPowerShell\v1.0\Modules'
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest().upper()
    scripts=PROJECT/'scripts/outbound-audit';run_id=uuid.uuid4().hex
    roles=['update-protected-reader.ps1','inventory-protected-audit.ps1','verify-protected-reader.ps1',
           'protected-file-utils.ps1','run-runtime-tests.ps1','test-system-runtime.ps1','cleanup-runtime-test.ps1']
    reader=args.build.parent/'first/bridge-outbound-reader.exe';supervisor=scripts/'run-protected-reader.ps1'
    if sha(reader)!=args.expect_reader_sha256.upper() or sha(scripts/'elevated-install-bootstrap.ps1')!=args.expect_bootstrap_sha256.upper():raise ValueError('Reviewed reader/bootstrap pin mismatch before UAC')
    payload=dict(setup=r'C:\Program Files\BridgeMonitorAudit.Setup-'+uuid.uuid4().hex,
        operator_sid=json.loads(Path(r'C:\Program Files\BridgeMonitorAudit\installation.json').read_text(encoding='utf-8-sig'))['operator_sid'],
        reader=str(reader),supervisor=str(supervisor),build=str(args.build),reader_sha256=sha(reader),
        supervisor_sha256=sha(supervisor),build_sha256=sha(args.build),runtime_run=run_id,test_pid=os.getpid(),watchdog_drill=args.watchdog_drill,
        scripts=[dict(name=n,source=str(scripts/n),sha256=sha(scripts/n)) for n in roles])
    bootstrap=(scripts/'elevated-install-bootstrap.ps1').read_text()
    command='& {\n'+bootstrap+'\n} '+"'"+base64.b64encode(json.dumps(payload).encode()).decode()+"'"
    encoded=base64.b64encode(command.encode('utf-16le')).decode()
    if len(encoded)>30000:raise RuntimeError('Bootstrap exceeds bounded command size')
    (args.artifacts/'payload.json').write_text(json.dumps(payload,indent=2))
    (args.artifacts/'bootstrap-command.ps1').write_text(command)
    print(json.dumps({'uac_pins_verified':True,'reader_sha256':sha(reader),'bootstrap_sha256':sha(scripts/'elevated-install-bootstrap.ps1')}),flush=True)
    data=Path(r'C:\Program Files\BridgeMonitorAudit\verification')/run_id
    journal=Journal(args.artifacts/'runtime.sqlite');born=creation_time(c.windll.kernel32.GetCurrentProcess())
    journal.register(os.getpid(),born,{'agent':'AuditRuntimeVerification','run':run_id})
    consumer=SpoolConsumer(journal,data/'metadata.tsv',args.artifacts/'cursor.json')
    refresh_addresses(consumer.collector)
    info=ShellExecuteInfo();info.cbSize=c.sizeof(info);info.fMask=0x40;info.lpVerb='runas'
    info.lpFile=r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    info.lpParameters='-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -EncodedCommand '+encoded
    info.lpDirectory=r'C:\Windows\System32';info.nShow=0
    shell=c.WinDLL('shell32',use_last_error=True);shell.ShellExecuteExW.argtypes=[c.POINTER(ShellExecuteInfo)]
    if not shell.ShellExecuteExW(c.byref(info)):raise c.WinError(c.get_last_error())
    result=dict(run_id=run_id,pid=os.getpid(),parent_pid=os.getppid(),birth=born,phases=[],production_tasks_started=False)
    deadline=time.monotonic()+120
    def wait(predicate):
        while time.monotonic()<deadline:
            consumer.tick()
            failure=Path(payload['setup'])/'result.json'
            if failure.exists():
                status=json.loads(failure.read_text(encoding='utf-8-sig'))
                if not status['installed']:raise RuntimeError('Protected controller failed: '+str(status.get('error')))
            value=predicate()
            if value:return value
            time.sleep(.15)
        raise TimeoutError('Bounded runtime verification deadline')
    try:
        if args.watchdog_drill:
            wait((data/'phase1.json').exists)
            wait((data/'watchdog-result.json').exists)
            result['watchdog']=json.loads((data/'watchdog-result.json').read_text(encoding='utf-8-sig'))
            kernel=c.WinDLL('kernel32');kernel.GetExitCodeProcess.argtypes=[w.HANDLE,c.POINTER(w.DWORD)]
            exit_code=w.DWORD();kernel.GetExitCodeProcess(info.hProcess,c.byref(exit_code));result['controller_exit']=exit_code.value
            if exit_code.value!=91 or not result['watchdog']['temporary_task_absent'] or result['watchdog']['remaining_audit_sessions']:raise RuntimeError('Independent watchdog drill failed')
            result['status']='passed'
        for phase in ([] if args.watchdog_drill else [1,2]):
            marker=data/f'phase{phase}.json'
            wait(marker.exists);meta=json.loads(marker.read_text(encoding='utf-8-sig'))
            def generation_ready():
                spool=data/'metadata.tsv'
                if not spool.exists():return False
                lines=spool.read_bytes().splitlines()
                last=max((i for i,line in enumerate(lines) if line==b'gap'),default=-1)
                return b'health' in lines[last+1:]
            wait(generation_ready)
            earliest=time.time_ns()
            request=tls_request()
            if ipaddress.ip_address(request['ip']).is_loopback or request['ip'] in consumer.collector.local:raise RuntimeError('Synthetic destination is local')
            def matched():
                rows=[r['evidence'] for r in journal.records() if r['evidence'].get('pid')==os.getpid() and
                      r['evidence'].get('ip')==request['ip'] and r['evidence'].get('port')==443 and
                      r['evidence'].get('local_port')==request['local_port'] and r['evidence']['kind']=='transfer' and
                      r['evidence'].get('at_ns',0)>=earliest]
                return rows if sum(r.get('bytes_out',0) for r in rows)==request['tls_wire_bytes'] else None
            rows=wait(matched)
            if not all(r.get('birth')==born and r.get('parent_pid')==os.getppid() and r.get('run')==run_id for r in rows):raise RuntimeError('Process/run attribution mismatch')
            result['phases'].append(dict(phase=phase,reader=meta,request=request,kernel_bytes=sum(r['bytes_out'] for r in rows),sender_verified=True))
            result[f'etw_phase{phase}']=etw_probe(meta['reader_pid'],stop=(phase==2))
        if not args.watchdog_drill:
            done=Path(payload['setup'])/'result.json';wait(done.exists)
            result['controller']=json.loads(done.read_text(encoding='utf-8-sig'))
            result['system']=json.loads((data/'system-result.json').read_text(encoding='utf-8-sig'))
            result['cleanup']=json.loads((data/'controller-result.json').read_text(encoding='utf-8-sig'))
            if not result['controller']['installed'] or result['cleanup']['audit_sessions'] or not result['cleanup']['temporary_task_removed']:raise RuntimeError('Incomplete controller cleanup')
            result['status']='passed'
    except Exception as error:
        result.update(status='failed',error=type(error).__name__,reason=str(error))
    finally:
        # Even a client-side failure waits for the independent bounded cleanup.
        finish=(data/'watchdog-result.json') if args.watchdog_drill else Path(payload['setup'])/'result.json';cleanup_deadline=time.monotonic()+90
        while not finish.exists() and time.monotonic()<cleanup_deadline:time.sleep(.25)
        if not finish.exists():result.update(status='failed',cleanup_pending=True)
        else:result['bootstrap_final']=json.loads(finish.read_text(encoding='utf-8-sig'))
        consumer.close()
        kernel=c.WinDLL('kernel32');kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle(info.hProcess)
        (args.artifacts/'result.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
