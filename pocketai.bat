@echo off
setlocal EnableExtensions
rem Terminal AI assistant. Shares models, settings and chat history with the dashboard.
set "ROOT=%~dp0"
set "PY=%ROOT%runtime\windows\python\python.exe"
if not exist "%PY%" (
    echo [ERROR] The Windows runtime is missing. Run setup-windows.bat first.
    exit /b 1
)
"%PY%" "%ROOT%app\pocketai.py" %*
