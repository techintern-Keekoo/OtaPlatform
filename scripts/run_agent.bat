@echo off
rem Runs one rate-parity check. Windows Task Scheduler calls this at 10:00, 18:00, 22:00.
rem Read-only: the agent never books or pays.
cd /d "%~dp0.."
if not exist output mkdir output
call .venv\Scripts\activate.bat
echo ==== %date% %time% ==== >> output\agent.log
python -m rate_parity run --config config.example.yaml >> output\agent.log 2>&1
