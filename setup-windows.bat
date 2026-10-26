@echo off
setlocal EnableExtensions
title PocketAI USB - Setup
rem ---------------------------------------------------------------------------
rem  One-time ONLINE setup for Windows 10/11 x64. Normal use is fully offline.
rem  Downloads a portable Python (python.org embeddable, ~11 MB), then runs
rem  scripts\setup.py which fetches llama.cpp + Python packages and asks before
rem  downloading any AI model. Nothing is installed on this computer.
rem ---------------------------------------------------------------------------
set "ROOT=%~dp0"
set "PYDIR=%ROOT%runtime\windows\python"
set "PYURL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
set "PYSHA=4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"

if /I not "%PROCESSOR_ARCHITECTURE%"=="AMD64" if /I not "%PROCESSOR_ARCHITEW6432%"=="AMD64" (
    echo [ERROR] PocketAI USB needs 64-bit Windows on an Intel/AMD x64 processor.
    goto :fail
)

if exist "%PYDIR%\python.exe" goto :run_setup

echo == Downloading portable Python 3.12 (about 11 MB)
where curl.exe >nul 2>nul || (echo [ERROR] curl.exe not found. Windows 10 version 1803 or newer is required. & goto :fail)
where tar.exe  >nul 2>nul || (echo [ERROR] tar.exe not found. Windows 10 version 1803 or newer is required. & goto :fail)
set "ZIP=%TEMP%\pocketai-python-embed.zip"
curl.exe -fL --retry 3 --progress-bar -o "%ZIP%" "%PYURL%" || (echo [ERROR] Download failed. Check the internet connection. & goto :fail)

set "HASH="
for /f "skip=1 tokens=* delims=" %%h in ('certutil -hashfile "%ZIP%" SHA256') do if not defined HASH set "HASH=%%h"
set "HASH=%HASH: =%"
if /I not "%HASH%"=="%PYSHA%" (
    echo [ERROR] Checksum mismatch for the Python download. Expected %PYSHA% got %HASH%
    del "%ZIP%" >nul 2>nul
    goto :fail
)
echo    verified SHA-256

mkdir "%PYDIR%" 2>nul
tar.exe -xf "%ZIP%" -C "%PYDIR%" || (echo [ERROR] Could not extract Python. & goto :fail)
del "%ZIP%" >nul 2>nul
rem The ._pth file makes this Python ignore anything installed on the host computer.
(
    echo python312.zip
    echo .
    echo ..\site-packages
    echo ..\..\..\app
) > "%PYDIR%\python312._pth"

:run_setup
"%PYDIR%\python.exe" "%ROOT%scripts\setup.py" %*
if errorlevel 1 goto :fail
echo.
pause
exit /b 0

:fail
echo.
echo Setup did not finish. See docs\TROUBLESHOOTING.md
pause
exit /b 1
