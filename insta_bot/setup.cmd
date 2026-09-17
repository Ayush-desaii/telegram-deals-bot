@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call python.cmd -m venv .venv
if errorlevel 1 goto done
call python.cmd -m pip install -r requirements.txt
if errorlevel 1 goto done
call python.cmd bot.py --setup
:done
pause
