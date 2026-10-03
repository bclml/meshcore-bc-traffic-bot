@echo off
rem Starts the GUI without a console window (pythonw); falls back to a minimized console if pythonw is missing.
cd /d "%~dp0"
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw mcIRC.py %*
) else (
    start "" /min python mcIRC.py %*
)
