"""Record effective ordinary-token permissions; opens existing objects without writing."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


class SidAttributes(c.Structure):
    _fields_ = [('sid', w.LPVOID), ('attributes', w.DWORD)]


class Groups(c.Structure):
    _fields_ = [('count', w.DWORD), ('groups', SidAttributes*1)]


class Mapping(c.Structure):
    _fields_ = [('read', w.DWORD), ('write', w.DWORD), ('execute', w.DWORD), ('all', w.DWORD)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--configuration', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows required')
    k = c.WinDLL('kernel32', use_last_error=True)
    a = c.WinDLL('advapi32', use_last_error=True)
    k.GetCurrentProcess.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    k.LocalFree.argtypes = [w.LPVOID]
    k.CreateFileW.argtypes = [w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
    k.CreateFileW.restype = w.HANDLE
    a.OpenProcessToken.argtypes = [w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]
    a.GetTokenInformation.argtypes = [w.HANDLE,c.c_int,w.LPVOID,w.DWORD,c.POINTER(w.DWORD)]
    a.ConvertSidToStringSidW.argtypes = [w.LPVOID,c.POINTER(w.LPVOID)]
    a.DuplicateToken.argtypes = [w.HANDLE,c.c_int,c.POINTER(w.HANDLE)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR,w.DWORD,c.POINTER(w.LPVOID),c.POINTER(w.DWORD)]
    a.AccessCheck.argtypes = [w.LPVOID,w.HANDLE,w.DWORD,c.POINTER(Mapping),w.LPVOID,c.POINTER(w.DWORD),c.POINTER(w.DWORD),c.POINTER(w.BOOL)]
    def checked(value):
        if not value:
            raise c.WinError(c.get_last_error())
    def sid(pointer):
        text = w.LPVOID()
        checked(a.ConvertSidToStringSidW(pointer,c.byref(text)))
        try:
            return c.wstring_at(text)
        finally:
            k.LocalFree(text)
    token, duplicate = w.HANDLE(), w.HANDLE()
    checked(a.OpenProcessToken(k.GetCurrentProcess(),10,c.byref(token)))
    try:
        def information(kind):
            size = w.DWORD()
            a.GetTokenInformation(token,kind,None,0,c.byref(size))
            if not 0 < size.value < 1024*1024:
                raise ValueError('invalid token-information size')
            data = c.create_string_buffer(size.value)
            checked(a.GetTokenInformation(token,kind,data,len(data),c.byref(size)))
            return data
        elevated = w.DWORD.from_buffer(information(20)).value
        if elevated:
            parser.error('permission evidence must use a non-elevated token')
        user = information(1)
        integrity = information(25)
        group_data = information(2)
        count = w.DWORD.from_buffer(group_data).value
        members = (SidAttributes*count).from_buffer(group_data,Groups.groups.offset)
        token_info = dict(user_sid=sid(SidAttributes.from_buffer(user).sid), elevated=False,
            elevation_type=w.DWORD.from_buffer(information(18)).value,
            integrity_sid=sid(SidAttributes.from_buffer(integrity).sid),
            groups=[dict(sid=sid(g.sid),attributes_hex=f'0x{g.attributes:08X}',deny_only=bool(g.attributes&16),enabled=bool(g.attributes&4)) for g in members])
        project = Path(__file__).resolve().parents[2]
        args.report.resolve().relative_to(project/'var')
        if args.report.exists():
            parser.error('report exists; choose a fresh evidence filename')
        root = Path(r'C:\Program Files\BridgeMonitorAudit')
        objects = [root,*root.rglob('*')]
        # Protect executable search ancestors as well as the installation itself.
        objects += [Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'),
                    *[Path(r'C:\Windows\System32')/n for n in ['logman.exe','ADVAPI32.dll','tdh.dll','WS2_32.dll','KERNEL32.dll']]]
        file_checks = []
        for path in objects:
            rights = dict(read=0x80000000,write=0x40000000,delete=0x10000,change_acl=0x40000,change_owner=0x80000)
            if path.is_dir():
                rights.update(create_file=2,create_subdirectory=4,delete_child=0x40)
            for label,mask in rights.items():
                handle = k.CreateFileW(str(path),mask,7,None,3,0x02000000 if path.is_dir() else 0,None)
                allowed = handle != w.HANDLE(-1).value
                error = 0 if allowed else c.get_last_error()
                if allowed:
                    k.CloseHandle(handle)
                file_checks.append(dict(path=str(path),right=label,requested_access=f'0x{mask:08X}',allowed=allowed,windows_error=error))
                if allowed != (label=='read') or (not allowed and error!=5):
                    raise RuntimeError(f'Unexpected access result: {path}, {label}, error {error}')
        for path in [Path('C:/'),Path('C:/Program Files'),Path(r'C:\Windows'),Path(r'C:\Windows\System32'),Path(r'C:\Windows\System32\WindowsPowerShell'),Path(r'C:\Windows\System32\WindowsPowerShell\v1.0')]:
            for label,mask in dict(delete_child=0x40,change_acl=0x40000,change_owner=0x80000).items():
                handle = k.CreateFileW(str(path),mask,7,None,3,0x02000000,None)
                allowed = handle != w.HANDLE(-1).value
                error = 0 if allowed else c.get_last_error()
                if allowed:
                    k.CloseHandle(handle)
                file_checks.append(dict(path=str(path),right=label,requested_access=f'0x{mask:08X}',allowed=allowed,windows_error=error))
                if allowed or error!=5:
                    raise RuntimeError('Unexpected ancestor access: '+str(path))
        checked(a.DuplicateToken(token,2,c.byref(duplicate)))
        configuration = json.loads(args.configuration.read_text(encoding='utf-8-sig'))
        task_checks = []
        for task in configuration['tasks']:
            descriptor = w.LPVOID()
            checked(a.ConvertStringSecurityDescriptorToSecurityDescriptorW(task['sddl'],1,c.byref(descriptor),None))
            try:
                for label,mask in dict(read=0x120089,execute=0x1200a0,write=0x120116,delete=0x10000,change_acl=0x40000,change_owner=0x80000).items():
                    mapping = Mapping(0x120089,0x120116,0x1200a0,0x1f01ff)
                    privileges = c.create_string_buffer(4096)
                    size,granted,allowed = w.DWORD(4096),w.DWORD(),w.BOOL()
                    checked(a.AccessCheck(descriptor,duplicate,mask,c.byref(mapping),privileges,c.byref(size),c.byref(granted),c.byref(allowed)))
                    task_checks.append(dict(task=task['name'],right=label,requested_access=f'0x{mask:08X}',allowed=bool(allowed.value)))
                    if bool(allowed.value)!=(label=='read'):
                        raise RuntimeError('Unexpected effective task permission: '+task['name']+' '+label)
            finally:
                k.LocalFree(descriptor)
        report = dict(at=datetime.now(timezone.utc).isoformat(),verified=True,token=token_info,
            file_checks=file_checks,task_checks=task_checks,
            configuration_sha256=hashlib.sha256(args.configuration.read_bytes()).hexdigest().upper(),
            harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest().upper(),
            method='CreateFileW OPEN_EXISTING and Windows AccessCheck with duplicated current token; no data writes or task mutations')
        with args.report.open('x',encoding='utf-8') as output:
            json.dump(report,output,indent=2)
        print(json.dumps({'verified':True,'file_checks':len(file_checks),'task_checks':len(task_checks),'elevated':False}))
    finally:
        if duplicate:
            k.CloseHandle(duplicate)
        k.CloseHandle(token)


if __name__ == '__main__':
    main()
