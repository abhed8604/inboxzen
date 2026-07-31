import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import async_session
from app.services.gmail_sync import sync_all_accounts
from app.services.triage_runner import run_triage_scan

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def sync_job():
    """Background job to sync emails and triage new ones"""
    try:
        async with async_session() as db:
            # Sync all accounts
            new_emails = await sync_all_accounts(db)
            
            # Triage any new emails
            if new_emails:
                await run_triage_scan(db)
                
            logger.info("Sync job completed. %d new emails found.", len(new_emails))
    except Exception as e:
        logger.error("Sync job failed: %s", e)

def start_scheduler():
    """Start the background scheduler"""
    if not scheduler.running:
        scheduler.add_job(
            func=sync_job,
            trigger=IntervalTrigger(minutes=5),
            id="email_sync_job",
            name="Email Sync Job",
            replace_existing=True
        )
        scheduler.start()
        logger.info("Scheduler started with 5-minute interval")