# InboxZen

Local-first, browser-based AI email triage for Gmail. Runs entirely on your machine with Python backend, HTML/CSS/HTMX frontend, and local decision model via Laya for triage.

## Features

- **Multi-account Gmail support** - Connect multiple Gmail accounts with distinct color coding
- **AI Triage** - Automatic email categorization, urgency scoring, and action detection using local Laya decision models
- **Real-time updates** - Live email updates via WebSocket
- **Dark/Light mode** - Theme toggle with persistence
- **Single-command startup** - `python start.py` handles everything

## Quick Start

1. Clone the repository
2. Run `python start.py`
3. Fill in your Google OAuth credentials in `.env` file
4. Run `python start.py` again
5. Open http://localhost:8000

## Prerequisites

- Python 3.11+
- Google Cloud project with Gmail API enabled

## Configuration

### Google OAuth Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable Gmail API
4. Create OAuth 2.0 credentials (Web application)
5. Add `http://localhost:8000/accounts/oauth2callback` as authorized redirect URI
6. Copy Client ID and Client Secret to `.env` file

### Environment Variables

Create `.env` file from `.env.example`:

```
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_REDIRECT_URI=http://localhost:8000/accounts/oauth2callback
SECRET_KEY=your_secret_key
```

## Architecture

### Tech Stack

- **Backend**: FastAPI + SQLAlchemy + SQLite
- **Frontend**: Jinja2 templates + HTMX + Vanilla CSS
- **AI**: Laya (local non-autoregressive decision model)
- **Real-time**: WebSocket via htmx-ext-ws

### Key Design Decisions

1. **Token Storage**: OAuth tokens are stored in plain text in local SQLite database. This is intentional for local-only application. A README warning is included.

2. **WebSocket Integration**: Uses htmx-ext-ws extension for cleaner integration with HTMX. Alternative was vanilla JavaScript, but htmx-ext-ws provides better HTMX ecosystem compatibility.

3. **Theme System**: Uses CSS variables with data-theme attribute for dark/light mode. Theme preference is persisted in localStorage and applied before page render to prevent flash of wrong theme.

4. **Database**: SQLite with aiosqlite for async support. Simple and requires no external database setup.

5. **Token Refresh**: Automatic token refresh using refresh tokens when access tokens expire.

### Folder Structure

```
inboxzen/
├── start.py                  # Single entrypoint
├── requirements.txt
├── .env.example
├── app/
│   ├── main.py              # FastAPI app
│   ├── config.py            # Configuration
│   ├── database.py          # SQLAlchemy setup
│   ├── models.py            # Database models
│   ├── schemas.py           # Pydantic schemas
│   ├── auth/
│   │   └── google_oauth.py  # OAuth2 flow
│   ├── services/
│   │   ├── llm_triage.py    # Laya triage engine
│   ├── routes/
│   │   ├── accounts.py      # Account management
│   │   ├── inbox.py         # Email views
│   │   ├── settings.py      # Settings
│   │   └── ws.py            # WebSocket
│   ├── websocket_manager.py # WebSocket management
│   └── templates/           # HTML templates
├── data/
│   └── inboxzen.db          # SQLite database
```

## Usage

### Connecting Accounts

1. Go to Settings page
2. Click "Connect Another Account"
3. Authorize with Google
4. Account appears with unique color

### Email Triage

- New emails are automatically synced every 5 minutes (configurable)
- Each email is triaged locally with Laya's non-autoregressive decision model
- Predicts category, urgency score (0-100), and action required

### Manual Sync

Click "Sync mails" in inbox to manually trigger sync.

### Filtering

- Filter by account using account sidebar items
- Filter by Important vs All
- Unread filter switch

## Development

### Running in Development

```bash
python start.py
```

This will:
1. Validate virtual environment (`.venv`)
2. Verify dependencies and Laya readiness
3. Start server with auto-reload

### Database

Database is automatically created on first run at `data/inboxzen.db`.

### Logs

Server logs are printed to console.

## Troubleshooting

### Laya Troubleshooting

- Preloaded into RAM automatically on startup.
- Can be manually loaded/unloaded in the sidebar or Settings modal.

### Gmail API Errors

- Verify OAuth credentials in `.env`
- Check redirect URI matches exactly
- Ensure Gmail API is enabled in Google Cloud Console

### Database Issues

- Delete `data/inboxzen.db` to reset
- Database is recreated on next startup
