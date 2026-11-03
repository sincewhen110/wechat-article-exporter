@echo off
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython

echo.
echo   WeChat article exporter
echo   starting local server ...
echo.
%PY% -u "%~dp0app.py" %*
echo.
echo   Server stopped, exit code %errorlevel%.
pause
exit /b 0

:nopython
echo.
echo   Python 3.8+ not found.
echo   Install it from https://www.python.org/downloads/ and tick
echo   "Add python.exe to PATH", then run this file again.
echo.
pause
exit /b 1
