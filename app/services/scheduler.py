from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import async_session
from app.services.gmail_sync import sync_all_accounts
from app.services.triage import triage_new_emails

scheduler = AsyncIOScheduler()

async def sync_job():
    """Background job to sync emails and triage new ones"""
    try:
        async with async_session() as db:
            # Sync all accounts
            new_emails = await sync_all_accounts(db)
            
            # Triage any new emails
            if new_emails:
                await triage_new_emails(db)
                
            print(f"Sync job completed. {len(new_emails)} new emails found.")
    except Exception as e:
        print(f"Sync job failed: {e}")

def start_scheduler():
    """Start the background scheduler"""
    if not scheduler.running:
        # Add sync job with default interval (5 minutes)
        scheduler.add_job(
            func=sync_job,
            trigger=IntervalTrigger(minutes=5),
            id="email_sync_job",
            name="Email Sync Job",
            replace_existing=True
        )
        scheduler.start()
        print("Scheduler started with 5-minute interval")

def update_scheduler_interval(minutes: int):
    """Update the scheduler interval"""
    if scheduler.running:
        scheduler.reschedule_job(
            "email_sync_job",
            trigger=IntervalTrigger(minutes=minutes)
        )
        print(f"Scheduler interval updated to {minutes} minutes")