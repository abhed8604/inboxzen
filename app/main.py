import os
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db, async_session
from app.routes import accounts, inbox, settings, ws
from app.services.gmail_sync import sync_and_triage

log_file = os.getenv("INBOXZEN_LOG_FILE")
logging.basicConfig(
    filename=log_file,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
for noisy in ("watchfiles", "aiosqlite", "httpcore", "httpx", "asyncio", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def _startup_sync():
    """Sync all accounts and triage new emails on first boot."""
    try:
        await asyncio.sleep(2)
        async with async_session() as db:
            count = await sync_and_triage(db)
            logger.info("Startup sync done. %d new emails.", count)
    except Exception as e:
        logger.error("Startup sync failed: %s", e)


@asynccontextmanager
async def lifespan(app):
    await init_db()
    from app.services.llm_providers import get_laya_router
    logger.info("Preloading Laya model into memory on startup...")
    await asyncio.to_thread(get_laya_router, True, "english")
    from app.services.scheduler import start_scheduler
    start_scheduler()
    asyncio.create_task(_startup_sync())
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
