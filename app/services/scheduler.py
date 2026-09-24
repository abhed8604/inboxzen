import asyncio
import logging

from app.database import async_session
from app.services.gmail_sync import sync_and_triage

logger = logging.getLogger(__name__)


async def _scheduler_loop():
    """Background task to sync and triage emails every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        try:
            async with async_session() as db:
                count = await sync_and_triage(db)
                logger.info("Scheduled sync completed. %d new emails.", count)
        except Exception as e:
            logger.error("Scheduled sync failed: %s", e)


def start_scheduler():
    """Start the background sync loop."""
    asyncio.create_task(_scheduler_loop())
    logger.info("Background sync loop started with 5-minute interval")