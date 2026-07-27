from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pathlib import Path
from typing import Optional

from app.database import get_db
from app.models import Email, Account
from app.services.gmail_sync import sync_all_accounts, mark_email_read, archive_email
from app.services.triage import triage_new_emails

router = APIRouter(tags=["inbox"])

# Setup Jinja2 templates
templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=templates_dir)

@router.get("/", response_class=HTMLResponse)
async def inbox(
    request: Request,
    account_id: Optional[int] = Query(None),
    priority: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Main inbox view with filtering"""
    # Build query with eager loading of account relationship
    query = select(Email).options(selectinload(Email.account)).where(Email.is_archived == False)
    
    # Apply filters
    if account_id:
        query = query.where(Email.account_id == account_id)
    if priority:
        query = query.where(Email.priority == priority)
    
    # Order by priority (CRITICAL first) then by received_at desc
    # Custom ordering for priority tiers
    priority_order = {
        "CRITICAL": 0,
        "HIGH": 1,
        "MEDIUM": 2,
        "LOW": 3,
        None: 4  # Untriaged emails at the bottom
    }
    
    result = await db.execute(query)
    emails = result.scalars().all()
    
    # Sort emails by priority tier then by received_at
    emails = sorted(emails, key=lambda e: (
        priority_order.get(e.priority, 4),
        -(e.received_at.timestamp() if e.received_at else 0)
    ))
    
    # Get all accounts for filter chips
    accounts_result = await db.execute(select(Account))
    accounts = accounts_result.scalars().all()
    
    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "emails": emails,
            "accounts": accounts,
            "selected_account_id": account_id,
            "selected_priority": priority,
            "priority_tiers": ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        }
    )

@router.post("/inbox/sync")
async def sync_now(db: AsyncSession = Depends(get_db)):
    """Manually trigger sync for all accounts"""
    new_emails = await sync_all_accounts(db)
    
    # Triage any new emails
    if new_emails:
        await triage_new_emails(db)
    
    count = len(new_emails)
    return f'<div class="sync-status success">Sync completed. {count} new email{"s" if count != 1 else ""} found.</div>'

@router.post("/inbox/{email_id}/read")
async def mark_as_read(request: Request, email_id: int, db: AsyncSession = Depends(get_db)):
    """Mark an email as read"""
    success = await mark_email_read(email_id, db)
    
    if not success:
        return {"error": "Failed to mark email as read"}
    
    # Get the updated email with account relationship loaded
    result = await db.execute(select(Email).options(selectinload(Email.account)).where(Email.id == email_id))
    email = result.scalar_one_or_none()
    
    # Return updated email card HTML
    return templates.TemplateResponse(
        request,
        "partials/email_card.html",
        {"email": email}
    )

@router.post("/inbox/{email_id}/archive")
async def archive(email_id: int, db: AsyncSession = Depends(get_db)):
    """Archive an email"""
    success = await archive_email(email_id, db)
    
    if not success:
        return {"error": "Failed to archive email"}
    
    # Return empty response since email should be removed from list
    return ""