@echo off
rem Removes the 3 daily runs created by install_schedule.bat.
schtasks /Delete /F /TN "Keekoo OTA Parity 10AM"
schtasks /Delete /F /TN "Keekoo OTA Parity 6PM"
schtasks /Delete /F /TN "Keekoo OTA Parity 10PM"
pause
