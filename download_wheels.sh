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
