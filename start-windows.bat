@echo off
setlocal EnableExtensions
title PocketAI USB
rem Starts the dashboard from wherever this folder is (any drive letter, paths with spaces).
set "ROOT=%~dp0"
set "PY=%ROOT%runtime\windows\python\python.exe"

if not exist "%PY%" (
    echo [ERROR] The Windows runtime is missing: "%PY%"
    echo         Run setup-windows.bat once while connected to the internet.
    pause
    exit /b 1
)

"%PY%" "%ROOT%app\pocketai.py" check
if errorlevel 1 (
    echo.
    echo [ERROR] PocketAI cannot start. Fix the problem marked [!!] above.
    pause
    exit /b 1
)
echo.
"%PY%" "%ROOT%app\pocketai.py" serve --open
if errorlevel 1 pause
exit /b 0
