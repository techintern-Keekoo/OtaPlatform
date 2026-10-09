@echo off
rem Double-click to check that this PC is ready for the scheduled rate-parity runs.
rem Read-only: it changes nothing, opens no OTA page and never books.
rem Add --offline to skip the browser start and the website search: doctor.bat --offline
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\activate.bat (
  echo The .venv folder is missing. Set up the project first: see README, Setup.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m rate_parity doctor --config config.example.yaml %*
set RESULT=%ERRORLEVEL%
echo.
pause
exit /b %RESULT%
