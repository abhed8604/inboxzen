import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.database import init_db, async_session
from app.routes import accounts, inbox, settings, ws
from app.services.llm_providers import start_ollama, stop_ollama

logger = logging.getLogger(__name__)


async def _startup_sync():
    """Sync all accounts and triage new emails on first boot."""
    try:
        await asyncio.sleep(2)
        async with async_session() as db:
            from app.services.gmail_sync import sync_all_accounts
            from app.services.triage_runner import run_triage_scan
            new_emails = await sync_all_accounts(db)
            if new_emails:
                await run_triage_scan(db)
            logger.info("Startup sync done. %d new emails.", len(new_emails))
    except Exception as e:
        logger.error("Startup sync failed: %s", e)


async def _get_provider_name() -> str:
    """Get the configured LLM provider name from the database."""
    try:
        async with async_session() as db:
            from sqlalchemy import select
            from app.models import Settings
            result = await db.execute(select(Settings).where(Settings.key == "llm_provider"))
            setting = result.scalar_one_or_none()
            return setting.value if setting and setting.value else "ollama"
    except Exception:
        return "ollama"


@asynccontextmanager
async def lifespan(app):
    provider_name = await _get_provider_name()
    if provider_name == "ollama":
        start_ollama()
    await init_db()
    from app.services.scheduler import start_scheduler
    start_scheduler()
    asyncio.create_task(_startup_sync())
    yield
    if provider_name == "ollama":
        stop_ollama()

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
