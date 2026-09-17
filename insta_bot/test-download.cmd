@echo off
cd /d "%~dp0"
call python.cmd bot.py --test-download
pause
