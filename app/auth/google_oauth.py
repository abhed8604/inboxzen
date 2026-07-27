from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import os

from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, GMAIL_SCOPES

def get_google_auth_url():
    """Generate Google OAuth2 authorization URL"""
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GMAIL_SCOPES,
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"  # Force consent to get refresh token
    )
    
    return authorization_url, state, flow

def exchange_code_for_tokens(code: str):
    """Exchange authorization code for access and refresh tokens"""
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GMAIL_SCOPES,
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    
    flow.fetch_token(code=code)
    credentials = flow.credentials
    
    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_expiry": credentials.expiry,
        "scopes": credentials.scopes
    }

def refresh_access_token(refresh_token: str):
    """Refresh an expired access token"""
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
    )
    
    credentials.refresh(Request())
    
    return {
        "access_token": credentials.token,
        "token_expiry": credentials.expiry
    }

def get_user_email(access_token: str, refresh_token: str):
    """Get the user's email address using the Gmail API"""
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
    )
    
    # Check if token is expired and refresh if needed
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    
    service = build("gmail", "v1", credentials=credentials)
    profile = service.users().getProfile(userId="me").execute()
    
    return profile["emailAddress"]

def get_gmail_service(access_token: str, refresh_token: str):
    """Get an authenticated Gmail API service"""
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
    )
    
    # Check if token is expired and refresh if needed
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    
    return build("gmail", "v1", credentials=credentials)