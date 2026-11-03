@echo off
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython

%PY% -u "%~dp0capture\reset_proxy.py"
echo.
echo   Network proxy restored. Try opening a web page now.
echo.
pause
exit /b 0

:nopython
echo.
echo   Python 3.8+ not found.
echo.
pause
exit /b 1
