@echo off
setlocal

:: ── Auto-elevate to administrator ──
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

title Intel Validation AI Agent
cd /d "%~dp0"

echo ===============================================
echo   Intel Validation AI Agent Launcher
echo ===============================================
echo.

set "VENV_DIR=.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [INFO] Virtual environment not found. Creating one now...
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 -m venv "%VENV_DIR%"
    ) else (
        where python >nul 2>nul
        if %errorlevel%==0 (
            python -m venv "%VENV_DIR%"
        ) else (
            echo [ERROR] Could not find Python on this machine.
            echo Install Python 3 and try again.
            pause
            exit /b 1
        )
    )
)

if not exist "%VENV_PYTHON%" (
    echo [ERROR] Failed to create virtual environment at %VENV_DIR%.
    pause
    exit /b 1
)

"%VENV_PYTHON%" -c "import flask, yaml, anthropic, httpx" >nul 2>nul
if not %errorlevel%==0 (
    echo [INFO] Installing required Python packages...
    "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
)

echo [OK] Using %VENV_PYTHON%
echo [OK] Starting web server... Browser will open automatically.
echo.
echo Press Ctrl+C in this window to stop the agent.
echo.

"%VENV_PYTHON%" web_ui.py

echo.
echo [INFO] Agent stopped.
pause
