@echo off
rem Stop the project-local Gemma classifier and release its GPU memory.
rem Stops only the process this project launched (PID, program and start time must match).
rem Safe to run when it is already stopped. Double-click it, or run it from a terminal.
cd /d "%~dp0.."
".venv\Scripts\python.exe" scripts\gemma-runtime.py stop
set RESULT=%ERRORLEVEL%
echo.
rem Exit code 3 means stopped (or nothing to stop); that is the normal result.
if "%RESULT%"=="3" (echo Gemma is stopped. The GPU is free.) else (echo Gemma may still be running. Exit code %RESULT%. See the message above.)
pause
exit /b %RESULT%
