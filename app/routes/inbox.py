import asyncio
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
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload, defer

from app.database import get_db
from app.models import Email, Account, Settings
from app.utils import _toast, _get_llm_ctx, get_selected_model
from app.services.gmail_sync import sync_all_accounts, sync_and_triage
from app.services.triage_runner import run_triage_scan, request_cancel, _scan_state
from app.services.llm_providers import is_laya_loaded, load_laya, unload_laya
from app.services.llm_triage import scan_email
from app.auth.google_oauth import get_gmail_service
from app.websocket_manager import broadcast

router = APIRouter(tags=["inbox"])
logger = logging.getLogger(__name__)

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=templates_dir)

IMPORTANT_SCORE = 70


def _email_query(account_id: Optional[int] = None, q: Optional[str] = None, important_only: bool = True):
    """Build the base email query with optional filters."""
    stmt = (
        select(Email)
        .options(
            defer(Email.body),
            selectinload(Email.account),
            selectinload(Email.attachments),
        )
        .where(Email.is_archived == False)
        .order_by(Email.received_at.desc().nullslast())
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
    return list(result.scalars().all())


@router.get("/", response_class=HTMLResponse)
async def inbox(
    request: Request,
    account_id: Optional[int] = Query(None),
    tab: Optional[str] = Query("important"),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    accounts_result = await db.execute(select(Account))
    accounts = accounts_result.scalars().all()

    total_count = (await db.execute(
        select(func.count(Email.id)).where(Email.is_archived == False)
    )).scalar_one() or 0

    if total_count == 0 and accounts:
        await sync_and_triage(db)
        total_count = (await db.execute(
            select(func.count(Email.id)).where(Email.is_archived == False)
        )).scalar_one() or 0

    unread_count = (await db.execute(
        select(func.count(Email.id)).where(Email.is_archived == False, Email.is_read == False)
    )).scalar_one() or 0

    important_count = (await db.execute(
        select(func.count(Email.id)).where(Email.is_archived == False, Email.relevance_score >= IMPORTANT_SCORE)
    )).scalar_one() or 0

    emails = await _fetch_inbox_emails(db, account_id, tab, q)

    selected_email = None
    if emails:
        first_email_res = await db.execute(
            select(Email)
            .options(selectinload(Email.account), selectinload(Email.attachments))
            .where(Email.id == emails[0].id)
        )
        selected_email = first_email_res.scalar_one_or_none()

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
            "total_count": total_count,
            "get_time_ago": get_time_ago,
            "theme": theme,
            **llm_ctx,
        }
    )


@router.get("/inbox/list", response_class=HTMLResponse)
async def inbox_list(
    request: Request,
    account_id: Optional[int] = Query(None),
    tab: Optional[str] = Query("important"),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    emails = await _fetch_inbox_emails(db, account_id, tab, q)

    unread_count = (await db.execute(
        select(func.count(Email.id)).where(Email.is_archived == False, Email.is_read == False)
    )).scalar_one() or 0

    important_count = (await db.execute(
        select(func.count(Email.id)).where(Email.is_archived == False, Email.relevance_score >= IMPORTANT_SCORE)
    )).scalar_one() or 0

    total_count = (await db.execute(
        select(func.count(Email.id)).where(Email.is_archived == False)
    )).scalar_one() or 0

    headers = {
        "X-Important-Count": str(important_count),
        "X-Total-Count": str(total_count),
        "X-Unread-Count": str(unread_count),
    }

    if not emails:
        return HTMLResponse(
            '<div class="empty-list-msg" style="padding:24px;text-align:center;color:var(--ash);font-size:13px;">No emails found</div>',
            headers=headers
        )

    return templates.TemplateResponse(
        request,
        "partials/email_list_items.html",
        {
            "emails": emails,
            "selected_email": emails[0],
            "get_time_ago": get_time_ago,
        },
        headers=headers
    )


@router.get("/inbox/{email_id}", response_class=HTMLResponse)
async def email_detail(
    request: Request,
    email_id: int,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Email)
        .options(selectinload(Email.account), selectinload(Email.attachments))
        .where(Email.id == email_id)
    )
    email = result.scalar_one_or_none()

    if not email:
        return HTMLResponse("<p>Email not found</p>", status_code=404)

    if not email.is_read:
        email.is_read = True
        await db.commit()

        if email.account and email.gmail_id:
            acc_token = email.account.access_token
            ref_token = email.account.refresh_token
            gmail_id = email.gmail_id

            async def _bg_mark_read():
                try:
                    svc = get_gmail_service(acc_token, ref_token)
                    await asyncio.to_thread(
                        svc.users().messages().modify(
                            userId="me",
                            id=gmail_id,
                            body={"removeLabelIds": ["UNREAD"]},
                        ).execute
                    )
                except Exception as e:
                    logger.debug("Background mark read failed: %s", e)

            asyncio.create_task(_bg_mark_read())

    email_links = extract_links(email.body)
    theme = request.cookies.get("inboxzen_theme", "dark")

    return templates.TemplateResponse(
        request,
        "partials/email_detail.html",
        {"email": email, "email_links": email_links, "theme": theme}
    )


@router.get("/inbox/{email_id}/attachment/{attachment_id}")
async def download_attachment(
    email_id: int,
    attachment_id: int,
    db: AsyncSession = Depends(get_db)
):
    from app.models import Attachment
    from app.services.gmail_sync import get_attachment_data
    from urllib.parse import quote

    result = await db.execute(
        select(Attachment)
        .options(selectinload(Attachment.email).selectinload(Email.account))
        .where(
            Attachment.id == attachment_id,
            Attachment.email_id == email_id
        )
    )
    attachment = result.scalar_one_or_none()
    if not attachment or not attachment.email or not attachment.email.account:
        return HTMLResponse("<p>Attachment not found</p>", status_code=404)

    try:
        content = await get_attachment_data(
            attachment.email.account,
            attachment.email.gmail_id,
            attachment.gmail_attachment_id
        )
        safe_filename = quote(attachment.filename or "attachment")
        disposition = "inline" if attachment.mime_type in ("application/pdf", "image/jpeg", "image/png", "image/gif", "text/plain") else "attachment"
        return Response(
            content=content,
            media_type=attachment.mime_type or "application/octet-stream",
            headers={"Content-Disposition": f'{disposition}; filename="{safe_filename}"'}
        )
    except Exception as e:
        logger.error("Failed to download attachment %s: %s", attachment_id, e)
        return HTMLResponse("<p>Failed to download attachment</p>", status_code=500)


@router.get("/inbox/{email_id}/body")
async def email_body(email_id: int, theme: str = Query("dark"), db: AsyncSession = Depends(get_db)):
    theme = theme.strip().lower() if theme else "dark"
    result = await db.execute(select(Email).where(Email.id == email_id))
    email = result.scalar_one_or_none()
    if not email or not email.body:
        return HTMLResponse("", status_code=404)

    if theme == "dark":
        base_style = "body{margin:0;padding:8px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;color:#e0e0e0;background:#0a0a0f;}img{max-width:100%;height:auto;}a{color:#7eb8ff;cursor:pointer;}table{border-collapse:collapse;}td,th{padding:4px 8px;}"
    else:
        base_style = "body{margin:0;padding:8px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px;color:#2B2620;background:#FBF6EA;}img{max-width:100%;height:auto;}a{color:#3b6dcc;cursor:pointer;}table{border-collapse:collapse;}td,th{padding:4px 8px;}"

    click_script = """<script>
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('a').forEach(function(a) {
    if (!a.getAttribute('target')) { a.setAttribute('target', '_blank'); }
    if (!a.getAttribute('rel')) { a.setAttribute('rel', 'noopener noreferrer'); }
  });
});
document.addEventListener('click', function(e) {
  var a = e.target.closest('a');
  if (a && a.href && !a.href.startsWith('javascript:')) {
    e.preventDefault();
    window.open(a.href, '_blank', 'noopener,noreferrer');
  }
}, true);
</script>"""

    html = f'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><base target="_blank"><style>{base_style}</style>{click_script}</head><body>{email.body}</body></html>'

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
    model = await get_selected_model(db)
    return JSONResponse({
        "online": True,
        "loaded": is_laya_loaded(),
        "model": model,
        "provider": "laya",
    })


@router.get("/api/scan-status")
async def scan_status_api():
    return JSONResponse(_scan_state)


@router.post("/laya/unload")
async def llm_unload():
    if await asyncio.to_thread(unload_laya):
        await broadcast({"type": "llm_status", "status": "unloaded"})
        return _toast("Laya unloaded from RAM.")
    return _toast("Model already unloaded.", "info")


@router.post("/laya/load")
async def llm_load(db: AsyncSession = Depends(get_db)):
    model = await get_selected_model(db)
    if await asyncio.to_thread(load_laya, model):
        await broadcast({"type": "llm_status", "status": "loaded"})
        return _toast("Laya loaded into RAM ✓", "success")
    return _toast("Failed to load Laya.", "error")



@router.post("/inbox/{email_id}/rescan")
async def rescan_single(email_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Email).options(selectinload(Email.account)).where(Email.id == email_id)
    )
    email = result.scalar_one_or_none()
    if not email:
        return _toast("Email not found", "error")

    try:
        model = await get_selected_model(db)
        rules_res = await db.execute(select(Settings).where(Settings.key == "ai_triage_rules"))
        rules_setting = rules_res.scalar_one_or_none()
        instructions = rules_setting.value if rules_setting and rules_setting.value else ""

        email_dict = {
            "id": email.id,
            "subject": email.subject or "",
            "sender": email.sender or "",
            "body": re.sub(r'<[^>]+>', '', (email.body or ""))[:1500],
        }
        triage_result = await scan_email(email_dict, model=model, instructions=instructions)
    except Exception as e:
        return _toast(f"Rescan failed: {e}", "error")

    email.summary = triage_result.summary or ""
    email.relevance_score = max(0, min(100, triage_result.score))
    email.category = triage_result.category or "Uncategorized"
    email.scan_model = model
    email.triaged_at = datetime.now(timezone.utc)
    await db.commit()

    score = email.relevance_score
    if score >= 90:
        ring_color = 'var(--signal)'
    elif score >= 70:
        ring_color = 'var(--amber)'
    else:
        ring_color = 'var(--ash)'

    ring_r = 35
    ring_circumf = round(2 * math.pi * ring_r, 4)
    ring_offset = round(ring_circumf * (1 - score / 100), 4)

    cat_badge = f'<span class="badge" style="display:inline-block;padding:3px 10px;border-radius:4px;background:rgba(255,255,255,0.08);color:var(--bone);font-size:12px;font-weight:500;">{email.category}</span>' if email.category else ''

    html = f'''<div id="zen-card" class="zen-card">
        <div class="zen-left">
            <div class="zen-heading">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3c.132 3.518 2.482 5.868 6 6-3.518.132-5.868 2.482-6 6-.132-3.518-2.482-5.868-6-6 3.518-.132 5.868-2.482 6-6z"/></svg>
                <span>Triage Decision</span>
            </div>
            <div class="zen-badges" style="display:flex;align-items:center;gap:8px;margin:4px 0 6px 0;">
                {cat_badge}
            </div>
            <div class="zen-footer">Decision by Laya · {email.scan_model or 'ModernBERT'}</div>
        </div>
        <div class="gauge-wrap">
            <div class="score-ring" style="width:84px;height:84px;">
                <svg viewBox="0 0 84 84" width="84" height="84" style="transform:rotate(-90deg);">
                    <circle cx="42" cy="42" r="{ring_r}" fill="none" stroke="var(--g-800)" stroke-width="6"/>
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
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
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
