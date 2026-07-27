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

def create_venv():
    """Create virtual environment if it doesn't exist"""
    venv_path = Path("venv")
    if not venv_path.exists():
        print("Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
        print("Virtual environment created successfully")
    return venv_path

def get_venv_python():
    """Get the path to the virtual environment's Python executable"""
    if sys.platform == "win32":
        return Path("venv/Scripts/python.exe")
    return Path("venv/bin/python")

def get_venv_pip():
    """Get the path to the virtual environment's pip executable"""
    if sys.platform == "win32":
        return Path("venv/Scripts/pip.exe")
    return Path("venv/bin/pip")

def install_dependencies():
    """Install dependencies from requirements.txt"""
    pip_path = get_venv_pip()
    if not pip_path.exists():
        print("Error: pip not found in virtual environment")
        sys.exit(1)
    
    print("Installing dependencies...")
    subprocess.run([str(pip_path), "install", "-r", "requirements.txt"], check=True)
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

def check_ollama():
    """Check if Ollama is running"""
    import httpx
    ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    try:
        response = httpx.get(f"{ollama_host}/api/tags", timeout=5.0)
        if response.status_code == 200:
            print("✓ Ollama is running")
            return True
        else:
            print(f"⚠ Ollama responded with status {response.status_code}")
            return False
    except Exception as e:
        print(f"⚠ Could not connect to Ollama at {ollama_host}: {e}")
        print("  Triage will fail gracefully until Ollama is running")
        return False

def start_server():
    """Start the FastAPI server"""
    python_path = get_venv_python()
    print("Starting InboxZen server...")
    print("Server will be available at http://localhost:8000")
    print("Press Ctrl+C to stop the server")
    
    # Run uvicorn with the FastAPI app
    subprocess.run([
        str(python_path), "-m", "uvicorn", 
        "app.main:app", 
        "--host", "0.0.0.0",
        "--port", "8000",
        "--reload"
    ], check=True)

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
    
    # Check Ollama (optional but recommended)
    check_ollama()
    
    # Start server
    start_server()

if __name__ == "__main__":
    main()