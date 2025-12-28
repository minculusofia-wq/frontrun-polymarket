#!/bin/bash
# Polymarket Frontrun Bot - Shell Launcher
# For Linux and macOS terminal usage

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  Polymarket Frontrun Bot"
echo "=========================================="

# Create venv if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt
pip install -q qasync

echo ""
echo "Starting bot GUI..."
echo "------------------------------------------"

# Run
python3 start.py
