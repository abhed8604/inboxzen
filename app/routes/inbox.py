from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone
import re
import httpx

from app.database import get_db
from app.models import Email, Account, Settings
from app.config import OLLAMA_HOST
from app.services.gmail_sync import sync_all_accounts, mark_email_read, trash_email
from app.services.triage import triage_new_emails

router = APIRouter(tags=["inbox"])

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=templates_dir)

IMPORTANT_SCORE = 70


async def _get_ollama_ctx(db: AsyncSession) -> dict:
    result = await db.execute(select(Settings).where(Settings.key == "ollama_model"))
    setting = result.scalar_one_or_none()
    model = (setting.value if setting and setting.value else None) or "qwen2.5:3b"
    online = False
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{OLLAMA_HOST}/api/tags", timeout=2.0)
            online = r.status_code == 200
    except Exception:
        pass
    return {"ollama_model_name": model, "ollama_online": online}


def get_time_ago(dt) -> str:
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h"
    elif seconds < 604800:
        days = seconds // 86400
        return f"{days}d" if days > 1 else "yesterday"
    else:
        return dt.strftime("%b %d")


def extract_links(body_text: str) -> list:
    if not body_text:
        return []
    url_pattern = r'https?://[^\s<>"\')\]\u2026]+'
    raw = re.findall(url_pattern, body_text)
    seen = set()
    unique = []
    for url in raw:
        cleaned = url.rstrip('.,;:!?)')
        if cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


@router.get("/", response_class=HTMLResponse)
async def inbox(
    request: Request,
    account_id: Optional[int] = Query(None),
    tab: Optional[str] = Query("important"),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    query = select(Email).options(selectinload(Email.account)).where(Email.is_archived == False)

    if account_id:
        query = query.where(Email.account_id == account_id)

    if tab == "important":
        query = query.where(Email.relevance_score >= IMPORTANT_SCORE)

    if q:
        search = f"%{q}%"
        query = query.where(
            or_(
                Email.sender.ilike(search),
                Email.subject.ilike(search),
                Email.snippet.ilike(search),
                Email.summary.ilike(search)
            )
        )

    result = await db.execute(query)
    emails = result.scalars().all()

    emails = sorted(emails, key=lambda e: (
        -(e.received_at.timestamp() if e.received_at else 0)
    ))

    accounts_result = await db.execute(select(Account))
    accounts = accounts_result.scalars().all()

    # All non-archived emails (unfiltered) for counts
    all_emails_result = await db.execute(
        select(Email).where(Email.is_archived == False)
    )
    all_emails = all_emails_result.scalars().all()

    # Auto-sync on first load if accounts exist but no emails
    if not all_emails and accounts:
        new_emails = await sync_all_accounts(db)
        if new_emails:
            await triage_new_emails(db)
        # Re-query after sync
        all_emails_result = await db.execute(
            select(Email).where(Email.is_archived == False)
        )
        all_emails = all_emails_result.scalars().all()
        # Re-query filtered emails too
        query = select(Email).options(selectinload(Email.account)).where(Email.is_archived == False)
        if account_id:
            query = query.where(Email.account_id == account_id)
        if tab == "important":
            query = query.where(Email.relevance_score >= IMPORTANT_SCORE)
        if q:
            search = f"%{q}%"
            query = query.where(
                or_(
                    Email.sender.ilike(search),
                    Email.subject.ilike(search),
                    Email.snippet.ilike(search),
                    Email.summary.ilike(search)
                )
            )
        result = await db.execute(query)
        emails = result.scalars().all()
        emails = sorted(emails, key=lambda e: (
            -(e.received_at.timestamp() if e.received_at else 0)
        ))

    unread_count = sum(1 for e in all_emails if not e.is_read)
    important_count = sum(1 for e in all_emails if (e.relevance_score or 0) >= IMPORTANT_SCORE)

    selected_email = emails[0] if emails else None

    theme_result = await db.execute(select(Settings).where(Settings.key == "theme"))
    theme_setting = theme_result.scalar_one_or_none()
    theme = theme_setting.value if theme_setting else "dark"

    ollama_ctx = await _get_ollama_ctx(db)

    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "emails": emails,
            "accounts": accounts,
            "selected_account_id": account_id,
            "selected_tab": tab or "important",
            "selected_email": selected_email,
            "email": selected_email,
            "search_query": q or "",
            "unread_count": unread_count,
            "important_count": important_count,
            "total_count": len(all_emails),
            "get_time_ago": get_time_ago,
            "theme": theme,
            **ollama_ctx,
        }
    )


@router.get("/inbox/{email_id}", response_class=HTMLResponse)
async def email_detail(
    request: Request,
    email_id: int,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Email).options(selectinload(Email.account)).where(Email.id == email_id)
    )
    email = result.scalar_one_or_none()

    if not email:
        return HTMLResponse("<p>Email not found</p>", status_code=404)

    if not email.is_read:
        await mark_email_read(email_id, db)
        email.is_read = True

    email_links = extract_links(email.body)

    return templates.TemplateResponse(
        request,
        "partials/email_detail.html",
        {"email": email, "email_links": email_links}
    )


@router.post("/inbox/sync")
async def sync_now(db: AsyncSession = Depends(get_db)):
    new_emails = await sync_all_accounts(db)
    if new_emails:
        await triage_new_emails(db)
    count = len(new_emails)
    return f'<div class="toast success">Sync completed. {count} new email{"s" if count != 1 else ""} found.</div>'


@router.post("/inbox/rescan")
async def rescan(db: AsyncSession = Depends(get_db)):
    count = await triage_new_emails(db)
    return f'<div class="toast success">Rescan complete. {count} email{"s" if count != 1 else ""} re-triaged.</div>'


@router.post("/inbox/{email_id}/archive")
async def archive(email_id: int, db: AsyncSession = Depends(get_db)):
    success = await trash_email(email_id, db)
    if not success:
        return HTMLResponse('<div class="toast error">Failed to archive email</div>')
    return ""
