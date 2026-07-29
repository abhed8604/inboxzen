from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.database import init_db
from app.routes import accounts, inbox, settings, ws

@asynccontextmanager
async def lifespan(app):
    await init_db()
    from app.services.scheduler import start_scheduler
    start_scheduler()
    yield

app = FastAPI(
    title="InboxZen",
    description="Local-first AI email triage for Gmail",
    lifespan=lifespan,
)

# Mount static files
static_dir = Path(__file__).parent / "templates" / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include routers
app.include_router(accounts.router)
app.include_router(inbox.router)
app.include_router(settings.router)
app.include_router(ws.router)