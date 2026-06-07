@echo off
title Portable AI - Fast Web Chat
color 0B

echo ===================================================
echo     Portable AI - Fast Web Chat Mode
echo ===================================================
echo.
echo  Launches the AI engine + browser chat UI.
echo  All chats auto-save to the USB drive.
echo.

:: Set paths to USB Shared Folder
set "OLLAMA_MODELS=%~dp0..\Shared\models\ollama_data"
set "OLLAMA_ORIGINS=*"
set "OLLAMA_HOST=127.0.0.1:11435"
set "OLLAMA_LIBRARY_PATH=%~dp0..\Shared\bin\lib\ollama"

:: -------------------------------------------------------
:: Find Python: prefer portable USB copy, then system
:: -------------------------------------------------------
set "PYTHON_CMD="

:: Check for portable Python bundled on USB
if exist "%~dp0..\Shared\python\python.exe" (
    set "PYTHON_CMD=%~dp0..\Shared\python\python.exe"
    echo [OK] Using portable Python from USB drive.
    goto :PythonReady
)

:: Check for system-installed Python
python --version >nul 2>&1
if %errorlevel%==0 (
    set "PYTHON_CMD=python"
    echo [OK] Using system Python.
    goto :PythonReady
)

:: Python not found anywhere
echo ===================================================
echo  ERROR: Python not found!
echo ===================================================
echo.
echo  Downloading portable Python to USB drive...
echo  (This only happens once, ~11MB download)
echo.

curl -L "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip" -o "%~dp0..\Shared\python-embed.zip"
if %errorlevel% neq 0 (
    echo Download failed. Please check your internet connection.
    pause
    exit
)

echo Extracting...
powershell -Command "Expand-Archive -Path '%~dp0..\Shared\python-embed.zip' -DestinationPath '%~dp0..\Shared\python' -Force"
del "%~dp0..\Shared\python-embed.zip" >nul 2>&1

if exist "%~dp0..\Shared\python\python.exe" (
    set "PYTHON_CMD=%~dp0..\Shared\python\python.exe"
    echo [OK] Portable Python installed on USB successfully!
) else (
    echo Failed to extract Python. Please try again.
    pause
    exit
)

:: -------------------------------------------------------
:: Start Ollama Engine
:: -------------------------------------------------------
:PythonReady

:: Force kill any previous instances to prevent port conflicts
taskkill /f /im ollama-windows.exe >nul 2>&1
taskkill /f /im ollama.exe >nul 2>&1

if not exist "%~dp0..\Shared\bin\ollama-windows.exe" (
    echo.
    echo ===================================================
    echo  ERROR: Ollama Engine Not Found!
    echo ===================================================
    echo.
    echo  It looks like the AI engine hasn't been set up yet.
    echo  Please double-click "install.bat" in the Windows 
    echo  folder first to safely download the components!
    echo.
    pause
    exit
)

:: Check if Ollama is already running
curl.exe -s http://127.0.0.1:11435/api/tags >nul 2>&1
if %errorlevel%==0 (
    echo [OK] Ollama engine is already running!
    goto :StartChat
)

echo Starting Ollama Engine...
start /b "" "%~dp0..\Shared\bin\ollama-windows.exe" serve

echo Waiting for engine to initialize...
set "WAIT_COUNT=0"
:WaitLoop
set /a "WAIT_COUNT+=1"
if %WAIT_COUNT% gtr 60 (
    echo.
    echo [ERROR] Engine failed to respond after 60 seconds.
    echo Please try running install.bat again to repair the engine.
    pause
    exit
)
timeout /t 1 /nobreak >nul
:: Use curl.exe explicitly to avoid issues with PowerShell aliases
curl.exe -s http://127.0.0.1:11435/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    <nul set /p="."
    goto :WaitLoop
)
echo.
echo [OK] Engine is online!


:: -------------------------------------------------------
:: Start Chat Server
:: -------------------------------------------------------
:StartChat
echo.
echo ===================================================
echo  AI ENGINE IS RUNNING
echo  Chat UI opening at: http://localhost:3333
echo  Close this window to shut down everything.
echo ===================================================
echo.

%PYTHON_CMD% "%~dp0..\Shared\chat_server.py"

echo Shutting down...
taskkill /f /im ollama-windows.exe >nul 2>&1
echo Done. Goodbye!
pause
