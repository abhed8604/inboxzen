import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

# Google OAuth2 settings
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/accounts/oauth2callback")

# Gmail API scopes
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify"
]

# Ollama settings
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Database settings
DATABASE_URL = "sqlite+aiosqlite:///./data/inboxzen.db"

# Secret key for session/cookie signing
SECRET_KEY = os.getenv("SECRET_KEY", "inboxzen-secret-key-change-in-production")

# Account color palette (for multi-account assignment)
ACCOUNT_COLORS = [
    "#3B82F6",  # Blue
    "#10B981",  # Green
    "#F59E0B",  # Amber
    "#EF4444",  # Red
    "#8B5CF6",  # Purple
    "#EC4899",  # Pink
    "#06B6D4",  # Cyan
    "#84CC16",  # Lime
]

# Default poll interval (in minutes)
DEFAULT_POLL_INTERVAL = 5

# Triage priority tiers
PRIORITY_TIERS = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]