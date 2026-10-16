import sys, pathlib, subprocess, json, difflib
root=pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0,str(root))
from tools.record_evidence import fingerprint,source_files
n=int(sys.argv[1]); command=[sys.executable,'-B','-m','unittest',*sys.argv[2:],'-v']
r=subprocess.run(command,cwd=root,capture_output=True,text=True)
ev=root/'docs/evidence/local00';(ev/f'task-{n:02d}-tests.log').write_text(r.stdout+r.stderr,encoding='utf-8')
print((r.stdout+r.stderr)[-3500:])
if r.returncode:raise SystemExit(r.returncode)
(ev/f'task-{n:02d}-source.json').write_text(json.dumps(fingerprint(root),indent=2))
chunks=[]
for p in source_files(root):
 old=root/'.baseline_local00'/p.relative_to(root)
 a=old.read_text(encoding='utf-8').splitlines(True) if old.exists() else []
 b=p.read_text(encoding='utf-8').splitlines(True)
 if a!=b: chunks.extend(difflib.unified_diff(a,b,fromfile='baseline/'+p.relative_to(root).as_posix(),tofile=p.relative_to(root).as_posix()))
(ev/f'task-{n:02d}.diff').write_text(''.join(chunks),encoding='utf-8')
with (pathlib.Path(__file__).parent/'progress.md').open('a',encoding='utf-8') as f:f.write(f'Task {n}: complete — tests {sys.argv[2:]} GREEN; evidence task-{n:02d}-tests.log, task-{n:02d}-source.json and diff.\n')
