@echo off
rem Start the project-local Gemma classifier on the RX 7900 XT (127.0.0.1:11435) and load the model.
rem Safe to run twice. Double-click it, or run it from a terminal.
cd /d "%~dp0.."
".venv\Scripts\python.exe" scripts\gemma-runtime.py start --warm
set RESULT=%ERRORLEVEL%
echo.
if "%RESULT%"=="0" (echo Gemma is running and using about 8.8 GiB of GPU memory. Run scripts\gemma-stop.cmd before gaming.) else (echo Gemma did not start cleanly. Exit code %RESULT%. See the message above.)
pause
exit /b %RESULT%
