@echo off
setlocal EnableExtensions EnableDelayedExpansion

:: ============================================================================
::  Intel Validation AI Agent - One-Time Environment Setup
::  Installs Python (if missing), the Python packages, and the required system
::  tools (ffmpeg/ffprobe and Tesseract-OCR). Re-runnable and idempotent.
:: ============================================================================

:: ── Auto-elevate to administrator (winget machine installs need it) ──
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

title Intel Validation AI Agent - Environment Setup
cd /d "%~dp0"

echo ===============================================
echo   AI Agent - One-Time Environment Setup
echo ===============================================
echo.

:: ── 0. Require winget ─────────────────────────────────────────────────────
where winget >nul 2>nul
if not %errorlevel%==0 (
    echo [ERROR] winget ^(App Installer^) was not found.
    echo Install "App Installer" from the Microsoft Store, then re-run this script.
    echo Alternatively, install ffmpeg and Tesseract-OCR manually.
    pause
    exit /b 1
)

:: ── 1. Ensure Python is available ─────────────────────────────────────────
call :find_python
if not defined PYTHON_CMD (
    echo [INFO] Python not found. Installing via winget...
    winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
    call :refresh_path
    call :find_python
)
if not defined PYTHON_CMD (
    echo [ERROR] Python is installed but not visible in this session.
    echo Close this window, open a new one, and run "Setup Environment.bat" again.
    pause
    exit /b 1
)
echo [OK] Using Python: %PYTHON_CMD%
echo.

:: ── 2. Create the virtual environment ─────────────────────────────────────
set "VENV_DIR=.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo [INFO] Creating virtual environment in "%VENV_DIR%"...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
)
if not exist "%VENV_PYTHON%" (
    echo [ERROR] Failed to create the virtual environment.
    pause
    exit /b 1
)

:: ── 3. Install the Python packages ────────────────────────────────────────
echo [INFO] Upgrading pip...
"%VENV_PYTHON%" -m pip install --upgrade pip --disable-pip-version-check
echo [INFO] Installing packages from requirements.txt...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
if not %errorlevel%==0 (
    echo [ERROR] pip failed to install one or more packages.
    pause
    exit /b 1
)
echo [OK] Python packages installed.
echo.

:: ── 4. Install the required system tools ──────────────────────────────────
call :ensure_pkg "Gyan.FFmpeg"              "ffmpeg (includes ffprobe)"
call :ensure_pkg "UB-Mannheim.TesseractOCR" "Tesseract-OCR"

:: Reload PATH so freshly installed tools are visible for verification below.
call :refresh_path
echo.

:: ── 5. Verify ─────────────────────────────────────────────────────────────
echo === Verification ===
call :verify_cmd ffprobe  "ffprobe"
call :verify_cmd ffmpeg   "ffmpeg"
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
    echo   [OK]   Tesseract-OCR found at "C:\Program Files\Tesseract-OCR\tesseract.exe"
) else (
    call :verify_cmd tesseract "Tesseract-OCR"
)
"%VENV_PYTHON%" -c "import flask, yaml, anthropic, httpx, PIL, numpy, scipy, pytesseract" >nul 2>nul
if %errorlevel%==0 (
    echo   [OK]   Core Python packages import cleanly.
) else (
    echo   [WARN] Some Python packages failed to import. Re-run this script.
)

echo.
echo ===============================================
echo   Setup complete.
echo ===============================================
echo   NOTE: If ffprobe/ffmpeg show [MISSING] above, they were just installed;
echo   their PATH entry takes effect in a NEW window. Close this one and launch
echo   the agent with "Launch Agent.bat".
echo.
echo   Bundled tools (hcitool.exe, ibterverify.exe, pwrtest.exe) ship with the
echo   repo. Intel WRT (cde.exe) is proprietary and must be installed separately.
echo.
pause
exit /b 0

:: ── Subroutines ───────────────────────────────────────────────────────────

:find_python
set "PYTHON_CMD="
where py >nul 2>nul && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>nul && set "PYTHON_CMD=python"
)
exit /b 0

:refresh_path
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%p"
exit /b 0

:ensure_pkg
set "PKG_ID=%~1"
set "PKG_NAME=%~2"
winget list --id "%PKG_ID%" -e --accept-source-agreements >nul 2>nul
if !errorlevel!==0 (
    echo [OK] %PKG_NAME% already installed.
) else (
    echo [INFO] Installing %PKG_NAME%...
    winget install --id "%PKG_ID%" -e --accept-package-agreements --accept-source-agreements
    if not !errorlevel!==0 echo [WARN] winget reported an issue installing %PKG_NAME%.
)
exit /b 0

:verify_cmd
where %~1 >nul 2>nul
if !errorlevel!==0 (
    echo   [OK]   %~2 found on PATH.
) else (
    echo   [MISSING] %~2 not on PATH yet ^(restart shell after install^).
)
exit /b 0
