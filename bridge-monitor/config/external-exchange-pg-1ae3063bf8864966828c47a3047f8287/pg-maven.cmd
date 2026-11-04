@echo off
"C:\Users\kian\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe" -NoLogo -NoProfile -NonInteractive -File "C:\AI\bridge-monitor\config\external-exchange-pg-1ae3063bf8864966828c47a3047f8287\verify.ps1"
exit /b %ERRORLEVEL%
