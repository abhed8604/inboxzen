# InboxZen

Local-first, browser-based AI email triage for Gmail. Runs entirely on your machine with Python backend, HTML/CSS/HTMX frontend, and local LLM via Ollama for triage.

## Features

- **Multi-account Gmail support** - Connect multiple Gmail accounts with distinct color coding
- **AI Triage** - Automatic email categorization using local Ollama models
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
- Ollama running locally (for AI triage)
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
OLLAMA_HOST=http://localhost:11434
SECRET_KEY=your_secret_key
```

## Architecture

### Tech Stack

- **Backend**: FastAPI + SQLAlchemy + SQLite
- **Frontend**: Jinja2 templates + HTMX + Vanilla CSS
- **AI**: Ollama (local LLM inference)
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
│   │   ├── gmail_sync.py    # Gmail API sync
│   │   ├── triage.py        # Ollama triage
│   │   └── scheduler.py     # Background jobs
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
- Each email is triaged with local Ollama model
- Priority levels: CRITICAL, HIGH, MEDIUM, LOW
- Categories: Job Offer, Bank/Finance, Newsletter, Personal, etc.

### Manual Sync

Click "Sync Now" button in inbox to manually trigger sync.

### Filtering

- Filter by account using account chips
- Filter by priority using priority chips
- Multiple filters can be combined

## Development

### Running in Development

```bash
python start.py
```

This will:
1. Create virtual environment
2. Install dependencies
3. Start server with auto-reload

### Database

Database is automatically created on first run at `data/inboxzen.db`.

### Logs

Server logs are printed to console. For production, configure logging in `app/main.py`.

## Troubleshooting

### Ollama Connection Issues

- Ensure Ollama is running: `ollama serve`
- Check Ollama host in `.env` file
- Triage will fail gracefully until Ollama is available

### Gmail API Errors

- Verify OAuth credentials in `.env`
- Check redirect URI matches exactly
- Ensure Gmail API is enabled in Google Cloud Console

### Database Issues

- Delete `data/inboxzen.db` to reset
- Database is recreated on next startup

## Future Enhancements (Out of Scope for MVP)

- [ ] Outlook/IMAP support
- [ ] Email composing/sending
- [ ] Threading/conversation view
- [ ] Attachments handling
- [ ] Mobile responsiveness
- [ ] Advanced visual design (glass/blur/AMOLED)
- [ ] Multi-provider support

## License

MIT License