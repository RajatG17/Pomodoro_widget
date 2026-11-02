@echo off
cd /d "%~dp0"
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw pomodoro.py
) else (
    start "" python pomodoro.py
)
exit /b 0