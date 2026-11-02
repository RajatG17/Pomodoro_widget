@echo off
cd /d "%~dp0"
pythonw pomodoro.py
if errorlevel 1 python pomodoro.py
