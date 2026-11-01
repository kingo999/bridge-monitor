"""Verify explicit operator registrations; Git association alone grants nothing."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess


def verify(project, origin, *, environment, expires_at=None):
    if expires_at is not None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError('worktree expiration must be timezone-aware')
        if expires_at <= datetime.now(timezone.utc):
            raise PermissionError('worktree grant expired')
    def git(root, *args):
        try:
            value = subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
                '-C', str(root), 'rev-parse', *args], env=environment,
                stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, text=True).strip()
            return Path(value).resolve(strict=True)
        except (OSError, subprocess.SubprocessError):
            raise ValueError('worktree origin could not be verified') from None
    source_top = git(origin, '--show-toplevel')
    task_top = git(project, '--show-toplevel')
    if (source_top == task_top
            or Path(origin).relative_to(source_top) != Path(project).relative_to(task_top)
            or git(origin, '--path-format=absolute', '--git-common-dir') !=
               git(project, '--path-format=absolute', '--git-common-dir')):
        raise ValueError('worktree must match the registered origin and project subdirectory')
