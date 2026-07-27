from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
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

PRIORITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    None: 4
}

PRIORITY_SCORES = {
    "CRITICAL": 95,
    "HIGH": 80,
    "MEDIUM": 60,
    "LOW": 30
}

def get_initials(sender: str) -> str:
    """Extract initials from sender name"""
    if not sender:
        return "?"
    name = sender.split("<")[0].strip() if "<" in sender else sender.strip()
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper() if name else "?"

def get_time_ago(dt) -> str:
    """Convert datetime to relative time string"""
    if not dt:
        return ""
    from datetime import datetime
    now = datetime.utcnow()
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins}m"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h"
    elif seconds < 604800:
        days = seconds // 86400
        return f"{days}d" if days > 1 else "yesterday"
    else:
        return dt.strftime("%b %d")

@router.get("/", response_class=HTMLResponse)
async def inbox(
    request: Request,
    account_id: Optional[int] = Query(None),
    priority: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    """Main inbox view with filtering"""
    query = select(Email).options(selectinload(Email.account)).where(Email.is_archived == False)

    if account_id:
        query = query.where(Email.account_id == account_id)
    if priority:
        query = query.where(Email.priority == priority)
    if q:
        search = f"%{q}%"
        query = query.where(
            or_(
                Email.sender.ilike(search),
                Email.subject.ilike(search),
                Email.snippet.ilike(search)
            )
        )

    result = await db.execute(query)
    emails = result.scalars().all()

    emails = sorted(emails, key=lambda e: (
        PRIORITY_ORDER.get(e.priority, 4),
        -(e.received_at.timestamp() if e.received_at else 0)
    ))

    accounts_result = await db.execute(select(Account))
    accounts = accounts_result.scalars().all()

    # Count by priority
    all_emails_result = await db.execute(
        select(Email).where(Email.is_archived == False)
    )
    all_emails = all_emails_result.scalars().all()
    unread_count = sum(1 for e in all_emails if not e.is_read)
    high_count = sum(1 for e in all_emails if e.priority in ("CRITICAL", "HIGH"))
    medium_count = sum(1 for e in all_emails if e.priority == "MEDIUM")

    # Get first email detail for initial load
    selected_email = emails[0] if emails else None

    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "emails": emails,
            "accounts": accounts,
            "selected_account_id": account_id,
            "selected_priority": priority,
            "selected_email": selected_email,
            "priority_tiers": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
            "search_query": q or "",
            "unread_count": unread_count,
            "high_count": high_count,
            "medium_count": medium_count,
            "total_count": len(all_emails),
            "get_initials": get_initials,
            "get_time_ago": get_time_ago,
            "priority_scores": PRIORITY_SCORES
        }
    )

@router.get("/inbox/{email_id}", response_class=HTMLResponse)
async def email_detail(
    request: Request,
    email_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get email detail for right panel"""
    result = await db.execute(
        select(Email).options(selectinload(Email.account)).where(Email.id == email_id)
    )
    email = result.scalar_one_or_none()

    if not email:
        return HTMLResponse("<p>Email not found</p>", status_code=404)

    # Mark as read
    if not email.is_read:
        await mark_email_read(email_id, db)
        email.is_read = True

    return templates.TemplateResponse(
        request,
        "partials/email_detail.html",
        {
            "email": email,
            "get_initials": get_initials,
            "priority_scores": PRIORITY_SCORES
        }
    )

@router.post("/inbox/sync")
async def sync_now(db: AsyncSession = Depends(get_db)):
    """Manually trigger sync for all accounts"""
    new_emails = await sync_all_accounts(db)

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

    result = await db.execute(
        select(Email).options(selectinload(Email.account)).where(Email.id == email_id)
    )
    email = result.scalar_one_or_none()

    return templates.TemplateResponse(
        request,
        "partials/email_card.html",
        {"email": email, "get_initials": get_initials, "get_time_ago": get_time_ago}
    )

@router.post("/inbox/{email_id}/archive")
async def archive(email_id: int, db: AsyncSession = Depends(get_db)):
    """Archive an email"""
    success = await archive_email(email_id, db)

    if not success:
        return {"error": "Failed to archive email"}

    return ""

@router.get("/inbox/search/{query}", response_class=HTMLResponse)
async def search_emails(
    request: Request,
    query: str,
    db: AsyncSession = Depends(get_db)
):
    """Search emails for autocomplete"""
    search = f"%{query}%"
    result = await db.execute(
        select(Email)
        .options(selectinload(Email.account))
        .where(Email.is_archived == False)
        .where(
            or_(
                Email.sender.ilike(search),
                Email.subject.ilike(search)
            )
        )
        .limit(10)
    )
    emails = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "partials/search_results.html",
        {"emails": emails, "get_initials": get_initials}
    )
