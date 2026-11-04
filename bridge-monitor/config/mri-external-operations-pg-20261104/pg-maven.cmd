@echo off
"C:\Users\kian\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe" -NoLogo -NoProfile -NonInteractive -File "C:\AI\bridge-monitor\config\mri-external-operations-pg-20261104\verify.ps1"
exit /b %ERRORLEVEL%
