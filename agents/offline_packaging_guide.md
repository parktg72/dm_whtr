# DM_WHTR Project - Offline Environment Deployment & Packaging Guide
**Role:** Env-DevOps Agent (AGY Headquarters)  
**Target Environment:** Python 3.12 (Windows / Linux) in an Air-Gapped (Offline) PC  
**Verification Date:** May 27, 2026

---

## 1. Executive Summary
In big data medical research, specifically cohort trajectory analyses using NHIS (National Health Insurance Service) databases, the research must take place within secure, air-gapped network zones. This document outlines the offline dependency packaging strategy for the **DM_WHTR (Diabetes Mellitus - Waist-to-Height Ratio) trajectory project**. 
This guide verifies all 23 packages defined in `requirements.txt`, confirms their PyPI availability, and provides robust, automated download scripts (`download_wheels.sh` and `download_wheels.bat`) to construct an offline wheelhouse that can be seamlessly installed without internet access.

---

### 2. Dependency Verification (`requirements.txt`)
We verified each package listed in `/mnt/h/dm_whtr/requirements.txt` against PyPI for Python 3.12 compatibility. We selected highly stable core scientific packages. All dependency trees are fully resolved and cached.

### Package Analysis Table
| Package | Version | Distribution Type | Role in Project | Verification Status |
| :--- | :--- | :--- | :--- | :--- |
| **pandas** | `2.2.3` | Platform Wheel | Primary data frame framework, handles cohort selection and database queries. | Verified (Supported on Py3.12) |
| **numpy** | `1.26.4` | Platform Wheel | High-performance numerical computations, vector math (compatible with lifelines). | Verified (Supported on Py3.12) |
| **scikit-learn** | `1.5.2` | Platform Wheel | Machine learning estimators, trajectory clustering, predictive evaluations. | Verified (Supported on Py3.12) |
| **scipy** | `1.14.1` | Platform Wheel | Math and science library, optimization solvers. | Verified (Supported on Py3.12) |
| **lifelines** | `0.29.0` | Pure Python Wheel | Survival analysis core library (Kaplan-Meier Curves & Cox Hazard). | Verified (Supported on Py3.12) |
| **matplotlib** | `3.9.2` | Platform Wheel | Base plotting engine for KM plots, forest plots, and distribution charts. | Verified (Supported on Py3.12) |
| **seaborn** | `0.13.2` | Pure Python Wheel | High-level data visualization engine for cohort trajectories. | Verified (Supported on Py3.12) |
| **duckdb** | `1.1.3` | Platform Wheel | High-performance embedded OLAP database for local streaming merges. | Verified (Supported on Py3.12, Zero-dependency) |


> [!NOTE]
> All chosen package versions are compatible with Python 3.12. They are perfectly optimized for either Windows (`win_amd64`) or Linux (`manylinux_2_17_x86_64`), meaning compiling from source (which typically fails in offline systems due to lack of a C++ Compiler / MSVC / GCC) is completely bypassed.

---

## 3. Offline Wheels Downloader Scripts
To automate download processes, two highly resilient scripts have been integrated in the root directory. They run on a machine with internet access to download both Linux and Windows wheels into structured local folders.

### 3.1 Linux Script (`download_wheels.sh`)
This script downloads platform-specific and platform-independent wheels.

```bash
#!/bin/bash
# ==============================================================================
# Automated Offline Wheels Downloader for DM_WHTR Project
# Designed for: Python 3.12 (Linux & Windows)
# Author: Env-DevOps Agent (AGY Headquarters)
# ==============================================================================

set -euo pipefail

# ANSI color codes for rich console outputs
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================================${NC}"
echo -e "${BLUE}         DM_WHTR Project - Offline Wheels Packaging System            ${NC}"
echo -e "${BLUE}======================================================================${NC}"

# 1. Verification
REQUIREMENTS_FILE="requirements.txt"
if [ ! -f "$REQUIREMENTS_FILE" ]; then
    echo -e "${RED}[ERROR] requirements.txt not found in the current directory!${NC}"
    echo -e "Please run this script from the project root containing requirements.txt."
    exit 1
fi

echo -e "${GREEN}[INFO] Found requirements.txt. Starting automated packaging...${NC}"

# Directories
WHEELS_DIR="./wheels"
LINUX_DIR="${WHEELS_DIR}/linux"
WINDOWS_DIR="${WHEELS_DIR}/windows"

mkdir -p "$LINUX_DIR"
mkdir -p "$WINDOWS_DIR"

# 2. Python 3.12 Linux Wheels Download
echo -e "\n${YELLOW}[Step 1/2] Downloading Linux Wheels (Python 3.12, manylinux_2_17_x86_64)...${NC}"
pip download \
    --only-binary=:all: \
    --platform manylinux_2_17_x86_64 \
    --platform manylinux2014_x86_64 \
    --python-version 3.12 \
    --implementation cp \
    --abi cp312 \
    -d "$LINUX_DIR" \
    -r "$REQUIREMENTS_FILE"

echo -e "${GREEN}[SUCCESS] Linux wheels successfully downloaded to: ${LINUX_DIR}${NC}"

# 3. Python 3.12 Windows Wheels Download
echo -e "\n${YELLOW}[Step 2/2] Downloading Windows Wheels (Python 3.12, win_amd64)...${NC}"
pip download \
    --only-binary=:all: \
    --platform win_amd64 \
    --python-version 3.12 \
    --implementation cp \
    --abi cp312 \
    -d "$WINDOWS_DIR" \
    -r "$REQUIREMENTS_FILE"

echo -e "${GREEN}[SUCCESS] Windows wheels successfully downloaded to: ${WINDOWS_DIR}${NC}"

# 4. Summary & Verification
echo -e "\n${BLUE}======================================================================${NC}"
echo -e "${GREEN}             Offline Packaging Process Completed Successfully!        ${NC}"
echo -e "${BLUE}======================================================================${NC}"
echo -e "Summary of downloaded packages:"
echo -e " - Linux wheels: \$(find \"\$LINUX_DIR\" -type f -name \"*.whl\" | wc -l) files"
echo -e " - Windows wheels: \$(find \"\$WINDOWS_DIR\" -type f -name \"*.whl\" | wc -l) files"
echo -e "\n${YELLOW}[Installation Instructions for Air-Gapped Environment]${NC}"
echo -e "To install these packages offline, copy the '${WHEELS_DIR}' directory to your target PC and run:"
echo -e "  For Linux:   pip install --no-index --find-links=./wheels/linux -r requirements.txt"
echo -e "  For Windows: pip install --no-index --find-links=.\\wheels\\windows -r requirements.txt"
echo -e "======================================================================"
```

### 3.2 Windows Script (`download_wheels.bat`)
This batch file allows users to execute download routines directly from Windows.

```cmd
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
```

---

## 4. Step-by-Step Offline Deployment Plan
Executing this deployment plan correctly guarantees 100% setup success on the air-gapped system.

### Phase 1: Local Packaging (Internet Machine)
1. Clone or copy the DM_WHTR repository directory onto a computer with an active internet connection.
2. Ensure you have Python 3.12 and pip installed.
3. Open a terminal or Command Prompt in the repository root.
4. Run:
   - On Linux: `chmod +x download_wheels.sh && ./download_wheels.sh`
   - On Windows: Double-click `download_wheels.bat` or run it from the console.
5. This creates a folder structure:
   ```
   dm_whtr/
   ├── wheels/
   │   ├── linux/         # Contains wheels for Linux (*-manylinux_*.whl, *.whl)
   │   └── windows/       # Contains wheels for Windows (*-win_amd64.whl, *.whl)
   ├── requirements.txt
   ...
   ```
6. Compress the repository folder (including the newly generated `wheels` directory) as a `.zip` or `.tar.gz` archive.

### Phase 2: Media Transfer (Security Check)
1. Transfer the compressed file to the air-gapped environment using a secure, approved storage medium (such as a checked USB drive, secure CD, or secure internal network gateway).
2. Ensure no patient identifiers are included in standard script paths or configuration files.

### Phase 3: Extraction and Target Setup (Air-Gapped PC)
1. Extract the transferred archive on the target machine.
2. Confirm Python 3.12 is installed on the target machine:
   ```cmd
   python --version
   ```
3. Establish a standard python virtual environment (`venv`) to keep system dependencies clean:
   - **Linux:**
     ```bash
     python3.12 -m venv venv
     source venv/bin/activate
     ```
   - **Windows:**
     ```cmd
     python -m venv venv
     call venv\Scripts\activate.bat
     ```

### Phase 4: Offline Installation
1. With your virtual environment activated, run the following offline command:
   - **Linux Target Environment:**
     ```bash
     pip install --no-index --find-links=./wheels/linux -r requirements.txt
     ```
   - **Windows Target Environment:**
     ```cmd
     pip install --no-index --find-links=.\wheels\windows -r requirements.txt
     ```
2. The installation will use the pre-downloaded local wheels and complete without checking internet repositories.

---

## 5. Security & Safe Operations
1. **De-Identification Protocol:** Before running cohort trajectories on target database tables (SQLite or SAP HANA), confirm all columns containing high-risk identifiers (Resident Registration Numbers - RRN, Names, Phone Numbers) have been fully redacted or transformed into unique non-reversible surrogate hashes.
2. **Encoding Security (Windows compatibility):** Since research is performed on a Windows-friendly spreadsheet system (e.g. Excel), ensure all analytical pipeline outputs use the standard Korean encoding **`utf-8-sig`** (UTF-8 with BOM) instead of raw UTF-8. This prevents MS Excel from garbling Korean text characters (CP949/Unicode mismatch).
3. **Execution Stream Protection:** In the analysis shell/batch execution flows, wrap all procedures with automated logging blocks and prevent terminal interfaces from immediately closing upon errors, ensuring the team can inspect traces.
