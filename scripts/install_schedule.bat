@echo off
rem Double-click once. Creates 3 daily runs on this PC: 10:00, 18:00, 22:00 (PC clock, keep it on IST).
rem The PC must be ON and you must be LOGGED IN at those times (the OTA check uses a visible Chrome window).
set RUNNER=%~dp0run_agent.bat
schtasks /Create /F /SC DAILY /ST 10:00 /TN "Keekoo OTA Parity 10AM" /TR "\"%RUNNER%\""
schtasks /Create /F /SC DAILY /ST 18:00 /TN "Keekoo OTA Parity 6PM" /TR "\"%RUNNER%\""
schtasks /Create /F /SC DAILY /ST 22:00 /TN "Keekoo OTA Parity 10PM" /TR "\"%RUNNER%\""
echo.
echo Done. Check them in Task Scheduler (search "Task Scheduler" in the Start menu).
echo Results: %~dp0..\output   (rate_parity.csv, report_*.txt, agent.log)
echo Check this PC any time: double-click %~dp0doctor.bat
pause
