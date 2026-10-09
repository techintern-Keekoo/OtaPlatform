@echo off
rem Runs one rate-parity check. Windows Task Scheduler calls this at 10:00, 18:00, 22:00.
rem Read-only: the agent never books or pays.
rem The run's exit code is passed on, so Task Scheduler's "Last Run Result" shows a failed run (0x0 = ok).
setlocal
cd /d "%~dp0.."
if not exist output mkdir output
rem Keep the log small: over 5 MB it is renamed to agent.log.1 (replacing the older copy).
if exist output\agent.log for %%F in (output\agent.log) do if %%~zF GTR 5242880 move /Y output\agent.log output\agent.log.1 >nul 2>&1
if not exist .venv\Scripts\activate.bat (
  echo ==== %date% %time% .venv is missing: set up the project first, see README Setup ==== >> output\agent.log
  exit /b 1
)
call .venv\Scripts\activate.bat
echo ==== %date% %time% ==== >> output\agent.log
python -m rate_parity run --config config.example.yaml >> output\agent.log 2>&1
set RESULT=%ERRORLEVEL%
echo ==== finished %date% %time% - exit code %RESULT% ==== >> output\agent.log
exit /b %RESULT%
