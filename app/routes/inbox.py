from datetime import datetime, timezone
import ipaddress
import logging
import math
from pathlib import Path
import re
import socket
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Email, Account, Settings
from app.utils import _toast, _get_llm_ctx, get_selected_model
from app.services.gmail_sync import sync_all_accounts, mark_email_read, trash_email
from app.services.triage_runner import run_triage_scan, request_cancel, _scan_state
from app.services.llm_triage import scan_email, load_custom_rules, TriageRateLimit
from app.services.llm_providers import get_provider

router = APIRouter(tags=["inbox"])
logger = logging.getLogger(__name__)

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=templates_dir)

IMPORTANT_SCORE = 70


def _email_query(account_id: Optional[int] = None, q: Optional[str] = None, important_only: bool = True):
    """Build the base email query with optional filters."""
    stmt = (
        select(Email)
        .options(selectinload(Email.account))
        .where(Email.is_archived == False)
    )
    if account_id:
        stmt = stmt.where(Email.account_id == account_id)
    if important_only:
        stmt = stmt.where(Email.relevance_score >= IMPORTANT_SCORE)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            or_(
                Email.subject.ilike(pattern),
                Email.sender.ilike(pattern),
                Email.summary.ilike(pattern),
                Email.snippet.ilike(pattern),
            )
        )
    return stmt


def get_time_ago(dt) -> str:
    if not dt:
        return ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    seconds = int((now - dt).total_seconds())
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h"
    elif seconds < 604800:
        days = seconds // 86400
        return f"{days}d" if days > 1 else "yesterday"
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


async def _fetch_inbox_emails(db: AsyncSession, account_id: Optional[int], tab: Optional[str], q: Optional[str]):
    query = _email_query(account_id, q, important_only=(tab == "important"))
    result = await db.execute(query)
    emails = list(result.scalars().all())
    emails.sort(key=lambda e: -(e.received_at.timestamp() if e.received_at else 0))
    return emails


@router.get("/", response_class=HTMLResponse)
async def inbox(
    request: Request,
    account_id: Optional[int] = Query(None),
    tab: Optional[str] = Query("important"),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    emails = await _fetch_inbox_emails(db, account_id, tab, q)

    accounts_result = await db.execute(select(Account))
    accounts = accounts_result.scalars().all()

    all_emails_result = await db.execute(
        select(Email).where(Email.is_archived == False)
    )
    all_emails = all_emails_result.scalars().all()

    if not all_emails and accounts:
        new_emails = await sync_all_accounts(db)
        if new_emails:
            await run_triage_scan(db)
        all_emails_result = await db.execute(
            select(Email).where(Email.is_archived == False)
        )
        all_emails = all_emails_result.scalars().all()
        emails = await _fetch_inbox_emails(db, account_id, tab, q)

    unread_count = sum(1 for e in all_emails if not e.is_read)
    important_count = sum(1 for e in all_emails if (e.relevance_score or 0) >= IMPORTANT_SCORE)
    selected_email = emails[0] if emails else None

    theme_result = await db.execute(select(Settings).where(Settings.key == "theme"))
    theme_setting = theme_result.scalar_one_or_none()
    theme = theme_setting.value if theme_setting else "dark"

    llm_ctx = await _get_llm_ctx(db)

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
            **llm_ctx,
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

    theme_result = await db.execute(select(Settings).where(Settings.key == "theme"))
    theme_setting = theme_result.scalar_one_or_none()
    theme = theme_setting.value if theme_setting else "dark"

    return templates.TemplateResponse(
        request,
        "partials/email_detail.html",
        {"email": email, "email_links": email_links, "theme": theme}
    )


@router.get("/inbox/{email_id}/body")
async def email_body(email_id: int, theme: str = Query("dark"), db: AsyncSession = Depends(get_db)):
    theme = theme.strip().lower() if theme else "dark"
    result = await db.execute(select(Email).where(Email.id == email_id))
    email = result.scalar_one_or_none()
    if not email or not email.body:
        return HTMLResponse("", status_code=404)

    if theme == "dark":
        base_style = "body{margin:0;padding:8px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;color:#e0e0e0;background:#0a0a0f;}img{max-width:100%;height:auto;}a{color:#7eb8ff;}table{border-collapse:collapse;}td,th{padding:4px 8px;}"
    else:
        base_style = "body{margin:0;padding:8px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;color:#2B2620;background:#FBF6EA;}img{max-width:100%;height:auto;}a{color:#3b6dcc;}table{border-collapse:collapse;}td,th{padding:4px 8px;}"

    html = f'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>{base_style}</style></head><body>{email.body}</body></html>'

    return HTMLResponse(
        content=html,
        media_type="text/html",
        headers={"Cache-Control": "private, no-cache"},
    )


@router.post("/inbox/sync")
async def sync_now(db: AsyncSession = Depends(get_db)):
    new_emails = await sync_all_accounts(db)
    if new_emails:
        await run_triage_scan(db)
    count = len(new_emails)
    return _toast(f"Sync completed. {count} new email{'s' if count != 1 else ''} found.")


@router.post("/inbox/rescan")
async def rescan(db: AsyncSession = Depends(get_db)):
    if _scan_state["running"]:
        return _toast("Scan already in progress", "error")
    result = await db.execute(select(Settings).where(Settings.key == "rescan_count"))
    setting = result.scalar_one_or_none()
    rescan_count = int(setting.value) if setting and setting.value else 50
    try:
        count = await run_triage_scan(db, rescan=True, limit=rescan_count)
    except TriageRateLimit:
        _scan_state["running"] = False
        return _toast("Rate limited by LLM provider. Wait a minute and try again.", "error")
    except Exception as e:
        logger.error("Rescan failed: %s", e)
        _scan_state["running"] = False
        return _toast(f"Rescan failed: {e}", "error")
    if count == -1:
        return _toast("LLM provider is not available. Check your settings.", "error")
    if count == 0:
        return _toast("No emails to rescan.")
    return _toast(f"Rescan complete. {count} email{'s' if count != 1 else ''} re-triaged.")


@router.post("/triage/cancel")
async def cancel_triage():
    request_cancel()
    return _toast("Scan cancelled.")


@router.get("/api/llm/status")
async def llm_status_api(db: AsyncSession = Depends(get_db)):
    provider = await get_provider(db)
    model = await get_selected_model(db)
    status = await provider.check_status(model)
    return JSONResponse({
        "online": status.online,
        "loaded": status.loaded,
        "model": model,
        "provider": provider.name,
    })


@router.get("/api/scan-status")
async def scan_status_api():
    return JSONResponse(_scan_state)


@router.post("/ollama/unload")
async def ollama_unload(db: AsyncSession = Depends(get_db)):
    provider = await get_provider(db)
    if not provider.supports_unload():
        return _toast("Unload is only available for Ollama", "error")
    from app.services.llm_providers import OllamaProvider
    op = OllamaProvider()
    model = await get_selected_model(db)
    ok = await op.unload(model)
    if ok:
        from app.websocket_manager import broadcast
        await broadcast({"type": "ollama_status", "status": "unloaded"})
        return _toast("Model unloaded from GPU.")
    return _toast("Failed to unload model.", "error")


@router.post("/inbox/{email_id}/archive")
async def archive(email_id: int, db: AsyncSession = Depends(get_db)):
    success = await trash_email(email_id, db)
    if not success:
        return _toast("Failed to archive email", "error")
    return HTMLResponse("")


@router.post("/inbox/{email_id}/rescan")
async def rescan_single(email_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Email).options(selectinload(Email.account)).where(Email.id == email_id)
    )
    email = result.scalar_one_or_none()
    if not email:
        return _toast("Email not found", "error")

    try:
        provider = await get_provider(db)
        model = await get_selected_model(db)
        rules = load_custom_rules()
        email_dict = {
            "id": email.id,
            "subject": email.subject or "",
            "sender": email.sender or "",
            "body": re.sub(r'<[^>]+>', '', (email.body or ""))[:1500],
        }
        triage_result = await scan_email(email_dict, provider, model, rules)
    except Exception as e:
        return _toast(f"Rescan failed: {e}", "error")

    email.summary = triage_result.summary or "No summary"
    email.relevance_score = max(0, min(100, triage_result.score))
    email.category = triage_result.category or "Uncategorized"
    email.action_required = triage_result.action_required
    email.scan_model = model
    email.triaged_at = datetime.now(timezone.utc)
    await db.commit()

    score = email.relevance_score
    if score >= 90:
        ring_color = '#4fd18b'
    elif score >= 70:
        ring_color = '#e8c34c'
    else:
        ring_color = '#9a9aa4'

    ring_r = 35
    ring_circumf = round(2 * math.pi * ring_r, 4)
    ring_offset = round(ring_circumf * (1 - score / 100), 4)

    html = f'''<div id="zen-card" class="zen-card">
        <div class="zen-left">
            <div class="zen-heading">
                <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l1.5 5.5L19 9l-5.5 1.5L12 16l-1.5-5.5L5 9l5.5-1.5L12 2z"/></svg>
                <span>Zen Summary</span>
            </div>
            <p class="zen-summary">{email.summary}</p>
            <div class="zen-footer">rated by AI</div>
        </div>
        <div class="gauge-wrap">
            <div class="score-ring" style="width:84px;height:84px;">
                <svg viewBox="0 0 84 84" width="84" height="84" style="transform:rotate(-90deg);">
                    <circle cx="42" cy="42" r="{ring_r}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="6"/>
                    <circle cx="42" cy="42" r="{ring_r}" fill="none" stroke="{ring_color}" stroke-width="6" stroke-linecap="round" stroke-dasharray="{ring_circumf}" stroke-dashoffset="{ring_offset}"/>
                </svg>
                <div class="score-ring-num" style="font-size:22px;color:{ring_color};">{score}</div>
            </div>
            <div class="gauge-label">Relevancy</div>
        </div>
    </div>'''

    return HTMLResponse(html)


_PROXY_ALLOWED_SCHEMES = {"http", "https"}
_PROXY_TIMEOUT = 10.0


def _is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in _PROXY_ALLOWED_SCHEMES or not parsed.hostname:
            return False
        addr_info = socket.getaddrinfo(parsed.hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return False
        return True
    except Exception:
        return False


@router.get("/proxy/image")
async def proxy_image(url: str = Query(...)):
    if not _is_safe_url(url):
        return Response(status_code=400, content="Invalid or restricted URL")

    try:
        async with httpx.AsyncClient(timeout=_PROXY_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "InboxZen/1.0"})
            if resp.status_code != 200:
                return Response(status_code=resp.status_code, content="Upstream error")
            content_type = resp.headers.get("content-type", "image/png")
            return Response(
                content=resp.content,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except Exception:
        return Response(status_code=502, content="Proxy fetch failed")
