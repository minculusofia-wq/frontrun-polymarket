#!/bin/bash
# Polymarket Frontrun Bot - macOS Quick Launcher
# Double-click this file in Finder to start the bot

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  Polymarket Frontrun Bot"
echo "=========================================="
echo ""

# Check for Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed!"
    echo "Please install Python 3.10+ from python.org"
    read -p "Press Enter to exit..."
    exit 1
fi

# Create venv if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate venv
source venv/bin/activate

# Check/install dependencies
echo "Checking dependencies..."
pip install -q -r requirements.txt 2>/dev/null

# Set PYTHONPATH
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

echo ""
echo "Starting bot TUI..."
echo "------------------------------------------"

# Run the TUI directly with proper path
python3 -m ui.app
