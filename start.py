#!/usr/bin/env python3
"""
Polymarket Frontrun Bot - Launcher.
Handles dependencies and starts the GUI application.
"""

import os
import sys
import subprocess
from pathlib import Path

# Project directory
PROJECT_DIR = Path(__file__).parent.absolute()
os.chdir(PROJECT_DIR)

# Add to Python path
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def get_venv_python() -> str:
    """Get virtual environment Python executable."""
    venv_path = PROJECT_DIR / 'venv'

    if sys.platform == 'win32':
        python_path = venv_path / 'Scripts' / 'python.exe'
    else:
        python_path = venv_path / 'bin' / 'python'

    if python_path.exists():
        return str(python_path)

    return sys.executable


def check_dependencies() -> bool:
    """Check if required dependencies are installed."""
    required = ['PySide6', 'qasync', 'py_clob_client', 'pydantic']

    for package in required:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            return False

    return True


def install_dependencies():
    """Install dependencies from requirements.txt."""
    print("Installing dependencies...")

    python = get_venv_python()
    req_file = PROJECT_DIR / 'requirements.txt'

    result = subprocess.run(
        [python, '-m', 'pip', 'install', '-r', str(req_file)],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"Failed to install dependencies: {result.stderr}")
        return False

    print("Dependencies installed successfully!")
    return True


def create_venv():
    """Create virtual environment if needed."""
    venv_path = PROJECT_DIR / 'venv'

    if not venv_path.exists():
        print("Creating virtual environment...")
        subprocess.run([sys.executable, '-m', 'venv', str(venv_path)])
        return True

    return False


def main():
    """Main launcher entry point."""
    print("=" * 50)
    print("  Polymarket Frontrun Bot")
    print("=" * 50)
    print()

    # Check for venv
    venv_created = create_venv()
    python = get_venv_python()

    print(f"Python: {python}")

    # Install dependencies if needed
    if venv_created or not check_dependencies():
        if not install_dependencies():
            print("Failed to install dependencies. Please install manually:")
            print("  pip install -r requirements.txt")
            sys.exit(1)

    print()
    print("Starting application...")
    print("-" * 50)

    # Set environment
    env = os.environ.copy()
    env['PYTHONPATH'] = str(PROJECT_DIR) + os.pathsep + env.get('PYTHONPATH', '')

    # Run the GUI
    result = subprocess.run(
        [python, '-m', 'ui.app'],
        cwd=str(PROJECT_DIR),
        env=env
    )

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
