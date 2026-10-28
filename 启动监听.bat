@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Chaoxing Monitor

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

echo ============================================
echo   Chaoxing Sign-in / Notice Monitor
echo   (Close this window to stop monitoring)
echo ============================================
echo.

if exist "%VENV_PY%" goto run

echo [*] First run: preparing environment (one time only)...
echo.

set "BOOT_PY="
where py >nul 2>nul
if %errorlevel%==0 set "BOOT_PY=py -3"
if defined BOOT_PY goto havepy
where python >nul 2>nul
if %errorlevel%==0 set "BOOT_PY=python"
if not defined BOOT_PY goto nopython

:havepy
echo [*] Creating virtual environment with: %BOOT_PY%
%BOOT_PY% -m venv "%~dp0.venv"
if errorlevel 1 goto venvfail

echo [*] Installing dependencies (requests / pycryptodome)...
"%VENV_PY%" -m pip install --upgrade pip -q
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt" -q
if errorlevel 1 goto depfail

echo [OK] Environment ready.
echo.

:run
"%VENV_PY%" chaoxing_monitor.py %*
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo [!] Program exited with code %RC% - see message above.
echo Press any key to close this window...
pause >nul
exit /b %RC%

:nopython
echo [x] Python not found.
echo     Please install Python 3.8+ and CHECK "Add Python to PATH" during setup.
echo     Download: https://www.python.org/downloads/
echo.
pause >nul
exit /b 1

:venvfail
echo [x] Failed to create the virtual environment. See the error above.
echo.
pause >nul
exit /b 1

:depfail
echo [x] Failed to install dependencies (usually a network issue).
echo     Try the mirror first, then run this file again:
echo     "%VENV_PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
pause >nul
exit /b 1
