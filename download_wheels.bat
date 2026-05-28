@echo off
rem ==============================================================================
rem Automated Offline Wheels Downloader for DM_WHTR Project
rem Designed for: Python 3.12 (Linux & Windows)
rem Author: Env-DevOps Agent (AGY Headquarters)
rem ==============================================================================

echo ======================================================================
echo          DM_WHTR Project - Offline Wheels Packaging System
echo ======================================================================

set REQUIREMENTS_FILE=requirements.txt
if not exist "%REQUIREMENTS_FILE%" (
    echo [ERROR] requirements.txt not found in the current directory!
    echo Please run this script from the project root containing requirements.txt.
    pause
    exit /b 1
)

echo [INFO] Found requirements.txt. Starting automated packaging...

set WHEELS_DIR=wheels
set LINUX_DIR=%WHEELS_DIR%\linux
set WINDOWS_DIR=%WHEELS_DIR%\windows

if not exist "%LINUX_DIR%" mkdir "%LINUX_DIR%"
if not exist "%WINDOWS_DIR%" mkdir "%WINDOWS_DIR%"

echo.
echo [Step 1/2] Downloading Linux Wheels (Python 3.12, manylinux_2_17_x86_64)...
pip download ^
    --only-binary=:all: ^
    --platform manylinux_2_17_x86_64 ^
    --platform manylinux2014_x86_64 ^
    --python-version 3.12 ^
    --implementation cp ^
    --abi cp312 ^
    -d "%LINUX_DIR%" ^
    -r "%REQUIREMENTS_FILE%"

if %ERRORLEVEL% neq 0 (
    echo [WARNING] Some Linux wheels failed to download. Check dependencies or pip version.
) else (
    echo [SUCCESS] Linux wheels successfully downloaded to: %LINUX_DIR%
)

echo.
echo [Step 2/2] Downloading Windows Wheels (Python 3.12, win_amd64)...
pip download ^
    --only-binary=:all: ^
    --platform win_amd64 ^
    --python-version 3.12 ^
    --implementation cp ^
    --abi cp312 ^
    -d "%WINDOWS_DIR%" ^
    -r "%REQUIREMENTS_FILE%"

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Windows wheels download failed!
    pause
    exit /b %ERRORLEVEL%
) else (
    echo [SUCCESS] Windows wheels successfully downloaded to: %WINDOWS_DIR%
)

echo ======================================================================
echo              Offline Packaging Process Completed Successfully!
echo ======================================================================
echo.
echo [Installation Instructions for Air-Gapped Environment]
echo To install these packages offline, copy the '%WHEELS_DIR%' directory to your target PC and run:
echo   For Linux:   pip install --no-index --find-links=./wheels/linux -r requirements.txt
echo   For Windows: pip install --no-index --find-links=.\wheels\windows -r requirements.txt
echo ======================================================================
pause
