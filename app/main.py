from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import os

from app.database import init_db
from app.routes import accounts, inbox, settings, ws

app = FastAPI(title="InboxZen", description="Local-first AI email triage for Gmail")

# Mount static files
static_dir = Path(__file__).parent / "templates" / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Setup Jinja2 templates
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=templates_dir)

# Include routers
app.include_router(accounts.router)
app.include_router(inbox.router)
app.include_router(settings.router)
app.include_router(ws.router)

@app.on_event("startup")
async def startup():
    # Initialize database tables
    await init_db()
    
    # Start scheduler if needed
    from app.services.scheduler import start_scheduler
    start_scheduler()

@app.get("/")
async def root(request: Request):
    # This will be overridden by inbox router, but kept as fallback
    return templates.TemplateResponse(request, "inbox.html")