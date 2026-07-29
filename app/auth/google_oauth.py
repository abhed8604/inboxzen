from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, GMAIL_SCOPES

_flow_store: dict[str, Flow] = {}


def _build_credentials(access_token: str, refresh_token: str) -> Credentials:
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
    )
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    return credentials


def _make_flow() -> Flow:
    return Flow.from_client_config(
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


def get_google_auth_url():
    flow = _make_flow()
    flow.redirect_uri = GOOGLE_REDIRECT_URI

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    _flow_store[state] = flow

    return authorization_url, state


def exchange_code_for_tokens(code: str, state: str):
    flow = _flow_store.pop(state, None)
    if not flow:
        raise ValueError("Invalid or expired OAuth state. Please try connecting again.")

    flow.redirect_uri = GOOGLE_REDIRECT_URI
    flow.fetch_token(code=code)
    credentials = flow.credentials

    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_expiry": credentials.expiry,
        "scopes": credentials.scopes,
    }


def refresh_access_token(refresh_token: str):
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
        "token_expiry": credentials.expiry,
    }


def get_user_email(access_token: str, refresh_token: str):
    credentials = _build_credentials(access_token, refresh_token)
    service = build("gmail", "v1", credentials=credentials)
    profile = service.users().getProfile(userId="me").execute()
    return profile["emailAddress"]


def get_gmail_service(access_token: str, refresh_token: str):
    credentials = _build_credentials(access_token, refresh_token)
    return build("gmail", "v1", credentials=credentials)
