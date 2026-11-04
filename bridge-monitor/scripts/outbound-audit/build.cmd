@echo off
setlocal
rem Run from an x64 Visual Studio developer prompt. Output goes to the specified directory.
if "%~1"=="" exit /b 2
if not exist "%~1" mkdir "%~1"
cl /nologo /EHsc /W4 /std:c++17 /O2 "%~dp0collector.cpp" /Fo"%~1\collector.obj" /Fe"%~1\bridge-outbound-reader.exe"
exit /b %ERRORLEVEL%
