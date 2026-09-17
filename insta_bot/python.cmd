@echo off
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" %*
  exit /b
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 %*
  exit /b
)
where python.exe >nul 2>nul
if not errorlevel 1 (
  python.exe %*
  exit /b
)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" %*
  exit /b
)
echo Python 3.11 or newer is required. Install it from python.org, then try again.
exit /b 1

