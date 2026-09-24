#!/usr/bin/env python3
"""
InboxZen Single-Command Startup Script
This script handles venv creation, dependency installation, and server launch.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def check_python_version():
    """Check if Python version is 3.11+"""
    if sys.version_info < (3, 11):
        print("Error: InboxZen requires Python 3.11 or later")
        print(f"Current version: {sys.version}")
        sys.exit(1)

def get_venv_python():
    """Get the path to the virtual environment's Python executable"""
    if sys.platform == "win32":
        return Path(".venv/Scripts/python.exe")
    return Path(".venv/bin/python")

def is_venv_valid():
    """Check if the virtual environment python is functional"""
    python_path = get_venv_python()
    if not python_path.exists():
        return False
    try:
        res = subprocess.run([str(python_path), "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except Exception:
        return False

def create_venv():
    """Create virtual environment if it doesn't exist or is invalid/moved"""
    venv_path = Path(".venv")
    if venv_path.exists() and not is_venv_valid():
        print("Existing virtual environment is invalid or was moved. Recreating...")
        shutil.rmtree(venv_path, ignore_errors=True)
        
    if not venv_path.exists():
        print("Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
        print("Virtual environment created successfully")
    return venv_path

def are_dependencies_installed():
    """Check if key dependencies are already installed in the venv."""
    python_path = get_venv_python()
    try:
        res = subprocess.run(
            [str(python_path), "-c", "import fastapi, uvicorn, sqlalchemy, laya"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return res.returncode == 0
    except Exception:
        return False

def install_dependencies():
    """Install dependencies from requirements.txt if needed."""
    if are_dependencies_installed():
        print("✓ Dependencies already installed")
        return

    python_path = get_venv_python()
    if not python_path.exists():
        print("Error: Python not found in virtual environment")
        sys.exit(1)
    
    print("Installing dependencies...")
    subprocess.run([str(python_path), "-m", "pip", "install", "-r", "requirements.txt"], check=True)
    print("Dependencies installed successfully")

def check_env_file():
    """Check if .env file exists, create from .env.example if not"""
    env_path = Path(".env")
    env_example_path = Path(".env.example")
    
    if not env_path.exists():
        if env_example_path.exists():
            print(".env file not found. Creating from .env.example...")
            shutil.copy(env_example_path, env_path)
            print("Please fill in your Google OAuth credentials in .env file")
            print("Required fields: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET")
            print("After filling in the credentials, run this script again.")
            sys.exit(0)
        else:
            print("Error: .env.example file not found")
            sys.exit(1)
    return env_path


def start_server():
    """Start the FastAPI server with all logging redirected to a timestamped log file."""
    python_path = get_venv_python()
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    from datetime import datetime
    log_path = logs_dir / f"inboxzen_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    print("Starting InboxZen server...")
    print("Server will be available at http://localhost:8000")
    print(f"Logging to: {log_path}")
    print("Press Ctrl+C to stop the server")

    env = os.environ.copy()
    env["INBOXZEN_LOG_FILE"] = str(log_path.resolve())

    uvicorn_cmd = [
        str(python_path), "-m", "uvicorn",
        "app.main:app",
        "--host", "0.0.0.0",
        "--port", "8000",
    ]
    if "--reload" in sys.argv:
        uvicorn_cmd.append("--reload")

    with open(log_path, "a", encoding="utf-8", buffering=1) as log_f:
        try:
            subprocess.run(
                uvicorn_cmd,
                env=env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                check=True,
            )
        except KeyboardInterrupt:
            print("\nServer stopped.")
        except subprocess.CalledProcessError as e:
            if e.returncode not in (0, -2, 130):
                print(f"\nServer exited with code {e.returncode}. See {log_path} for details.")

def main():
    """Main startup function"""
    print("InboxZen Startup")
    print("=" * 40)
    
    # Check Python version
    check_python_version()
    
    # Create virtual environment
    create_venv()
    
    # Install dependencies
    install_dependencies()
    
    # Check .env file
    check_env_file()
    
    # Start server
    start_server()

if __name__ == "__main__":
    main()