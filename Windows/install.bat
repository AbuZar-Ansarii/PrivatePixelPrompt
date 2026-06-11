@echo off
setlocal enabledelayedexpansion

title Portable AI - Setup
color 0E

echo ===================================================
echo     PORTABLE AI - USB SETUP
echo ===================================================
echo.
echo  This will download and configure AI models onto
echo  your USB drive. You'll get to CHOOSE which models
echo  to install from a curated list.
echo.
echo   - 6 preset models (uncensored + standard)
echo   - Custom model support (bring your own GGUF)
echo   - Minimum USB space: 8 GB (16 GB recommended)
echo.
echo  Make sure you have a good internet connection!
echo.

REM Check if PowerShell script exists
if not exist "%~dp0install-core.ps1" (
    echo.
    echo ===================================================
    echo     ERROR: install-core.ps1 not found
    echo ===================================================
    echo.
    pause
    exit /b 1
)

REM Run the PowerShell setup script from the same folder as this bat file
powershell -ExecutionPolicy Bypass -File "%~dp0install-core.ps1"
if %errorlevel% neq 0 (
    echo.
    echo ===================================================
    echo     SETUP FAILED
    echo ===================================================
    echo.
    echo Check the error messages above.
    echo.
    pause
    exit /b 1
)

echo.
echo ===================================================
echo     SETUP COMPLETE! You're ready to go!
echo ===================================================
echo.
echo  To start: double-click start-fast-chat.bat
echo.
pause
