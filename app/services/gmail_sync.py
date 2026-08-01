import asyncio
import base64
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html as html_lib
import logging
import re
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Account, Email
from app.auth.google_oauth import get_gmail_service, refresh_access_token

logger = logging.getLogger(__name__)


_SAFE_TAGS = frozenset({
    'a', 'p', 'br', 'b', 'i', 'u', 'em', 'strong', 'ul', 'ol', 'li',
    'h1', 'h2', 'h3', 'h4', 'span', 'div', 'blockquote', 'table', 'tr',
    'td', 'th', 'thead', 'tbody', 'pre', 'code', 'hr', 'img', 'style',
    'caption', 'colgroup', 'col', 'tfoot',
})

_TRACKING_DOMAINS = (
    r'myunidays\.com', r'bloomreach\.', r'link\.linkedin\.com',
    r'click\.mail\.', r'click\.google\.', r'tracking\.', r'pixel\.',
    r'open\.tracking\.', r'emails\.', r'e\.corp\.', r'rd\.google\.com',
    r'discover\.apple\.com', r'ba\.link', r'mkt\.', r'go\.appsflyer\.com',
    r't\.co', r'bit\.ly', r'goo\.gl', r'tinyurl\.com',
)


def _sanitize_html(raw: str) -> str:
    """Strip unsafe tags/attrs, neutralize tracking, keep safe HTML structure."""
    if not raw:
        return ""

    # Remove <script>, <iframe>, <object>, <embed>, <form>, <input>, <textarea>, <button>, <svg>
    text = re.sub(
        r'<(script|iframe|object|embed|form|input|textarea|button|svg)[^>]*>.*?</\1>',
        '', raw, flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r'<(script|iframe|object|embed|form|input|textarea|button|svg|meta|link)[^>]*/?\s*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</?head[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*on\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|\S+)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'href\s*=\s*(?:"javascript:[^"]*"|\'javascript:[^\']*\')', '', text, flags=re.IGNORECASE)

    def _tag_strip(m):
        tag = m.group(0)
        name = re.match(r'</?([a-zA-Z]+)', tag)
        if name and name.group(1).lower() in _SAFE_TAGS:
            return tag
        return ''

    text = re.sub(r'<[^>]+>', _tag_strip, text)
    for domain in _TRACKING_DOMAINS:
        text = re.sub(
            r'href\s*=\s*("https?://[^"]*' + domain + r'[^"]*"|\'https?://[^\']*' + domain + r'[^\']*\')',
            '', text, flags=re.IGNORECASE,
        )
    text = re.sub(r'(\?|&)(utm_\w+=[^&"\']*)&?', lambda m: '?' if m.group(0).startswith('?') else '', text)
    text = re.sub(r'\?$', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _plain_to_html(text: str) -> str:
    """Convert plain text to simple HTML with auto-linked URLs."""
    if not text:
        return ""
    text = html_lib.escape(text)
    text = re.sub(r'(https?://[^\s<>"]+)', r'<a href="\1" target="_blank" rel="noopener">\1</a>', text)
    return text.replace('\n', '<br>')


def _resolve_cid_refs(html: str, images: dict) -> str:
    """Replace cid: references in HTML with data: URIs from collected image parts."""
    if not images or 'cid:' not in html:
        return html

    for cid, (mime, b64data) in images.items():
        data_uri = f"data:{mime};base64,{b64data}"
        html = html.replace(f'cid:{cid}', data_uri)
        html = html.replace(f'cid:&lt;{cid}&gt;', data_uri)

    return html


_PROXY_IMG_PATTERN = re.compile(
    r'(<img\b[^>]*?\b)src=["\'](https?://[^"\']+)["\']',
    re.IGNORECASE
)


def _rewrite_image_urls(html: str) -> str:
    """Rewrite external <img src="https://..."> to /proxy/image?url=... so browser can load them."""
    def _replace(m):
        prefix = m.group(1)
        url = m.group(2)
        return f'{prefix}src="/proxy/image?url={quote(url, safe="")}"'

    return _PROXY_IMG_PATTERN.sub(_replace, html)


def _extract_body_and_attachments(payload: dict) -> tuple[str, str, dict, list]:
    """Extract HTML, plain text bodies, inline images, and file attachments from Gmail payload."""
    html_body = ""
    plain_body = ""
    images = {}
    attachments = []

    def _walk_part(part: dict):
        nonlocal html_body, plain_body
        mime = part.get("mimeType", "")
        body_data = part.get("body", {})
        data = body_data.get("data", "")
        att_id = body_data.get("attachmentId", "")
        filename = part.get("filename", "")
        size = body_data.get("size", 0)

        # Look in headers for filename if empty on part
        if not filename:
            for h in part.get("headers", []):
                hname = h.get("name", "").lower()
                hval = h.get("value", "")
                if hname in ("content-disposition", "content-type"):
                    m = re.search(r'(?:filename|name)\*?=(?:[^\'"]*\'[^\'"]*\')?["\']?([^"\';\r\n]+)["\']?', hval, re.I)
                    if m:
                        filename = m.group(1).strip()
                        break

        cid = ""
        disposition = ""
        for h in part.get("headers", []):
            hname = h.get("name", "").lower()
            if hname == "content-id":
                cid = h["value"].strip("<>")
            elif hname == "content-disposition":
                disposition = h["value"].lower()

        is_att = bool(filename or att_id or "attachment" in disposition)

        if is_att and att_id and not mime.startswith("multipart/"):
            if mime.startswith("image/") and cid and data and not filename and "attachment" not in disposition:
                images[cid] = (mime, data)
            else:
                default_name = "document.pdf" if mime == "application/pdf" else "attachment"
                attachments.append({
                    "filename": filename or default_name,
                    "mime_type": mime or "application/octet-stream",
                    "size": size,
                    "attachment_id": att_id
                })
        elif mime == "text/html" and data and not html_body:
            html_body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        elif mime == "text/plain" and data and not plain_body:
            plain_body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        elif mime.startswith("image/") and data and cid:
            images[cid] = (mime, data)

        for sub in part.get("parts", []):
            _walk_part(sub)

    _walk_part(payload)
    return html_body, plain_body, images, attachments

async def sync_account(account_id: int, db: AsyncSession):
    """Sync emails for a specific Gmail account"""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        return []
    
    if account.token_expiry:
        expiry = account.token_expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < datetime.now(timezone.utc):
            try:
                new_tokens = refresh_access_token(account.refresh_token)
                account.access_token = new_tokens["access_token"]
                account.token_expiry = new_tokens["token_expiry"]
                await db.commit()
            except Exception as e:
                logger.error("Failed to refresh token for account %s: %s", account.email, e)
                return []
    
    service = get_gmail_service(account.access_token, account.refresh_token)
    
    try:
        results = await asyncio.to_thread(
            service.users().messages().list(
                userId="me",
                maxResults=50,
                labelIds=["INBOX"]
            ).execute
        )
        
        messages = results.get("messages", [])
        new_emails = []
        
        for message in messages:
            gmail_id = message["id"]
            
            result = await db.execute(
                select(Email).where(
                    Email.gmail_id == gmail_id,
                    Email.account_id == account_id
                )
            )
            if result.scalar_one_or_none():
                continue
            
            msg = await asyncio.to_thread(
                service.users().messages().get(
                    userId="me",
                    id=gmail_id,
                    format="full"
                ).execute
            )
            
            headers = msg["payload"]["headers"]
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
            sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown Sender")
            date_str = next((h["value"] for h in headers if h["name"] == "Date"), None)
            
            received_at = datetime.now(timezone.utc)
            if date_str:
                try:
                    received_at = parsedate_to_datetime(date_str)
                except Exception:
                    pass
            
            # Get snippet, body, and attachments
            snippet = msg.get("snippet", "")
            html_body, plain_body, images, attachments_data = _extract_body_and_attachments(msg["payload"])

            if html_body:
                body = _sanitize_html(html_body)
                body = _resolve_cid_refs(body, images)
                body = _rewrite_image_urls(body)
            elif plain_body:
                body = html_lib.escape(plain_body).replace('\n', '<br>')
            else:
                body = snippet
            
            # Create email record
            email = Email(
                account_id=account_id,
                gmail_id=gmail_id,
                sender=sender,
                subject=subject,
                snippet=snippet,
                body=body,
                received_at=received_at,
                is_read="UNREAD" not in msg.get("labelIds", []),
                is_archived="TRASH" in msg.get("labelIds", [])
            )
            db.add(email)
            await db.flush()

            # Create attachment records
            for att in attachments_data:
                if att.get("attachment_id"):
                    attachment = Attachment(
                        email_id=email.id,
                        filename=att["filename"],
                        mime_type=att["mime_type"],
                        size=att["size"],
                        gmail_attachment_id=att["attachment_id"]
                    )
                    db.add(attachment)

            new_emails.append(email)
        
        await db.commit()
        
        # Return new emails for triage
        return new_emails
        
    except Exception as e:
        logger.error("Failed to sync account %s: %s", account.email, e)
        return []


async def get_attachment_data(account: Account, gmail_id: str, attachment_id: str) -> bytes:
    """Fetch raw attachment bytes from Gmail API."""
    service = get_gmail_service(account.access_token, account.refresh_token)
    res = await asyncio.to_thread(
        service.users().messages().attachments().get(
            userId="me",
            messageId=gmail_id,
            id=attachment_id
        ).execute
    )
    data = res.get("data", "")
    if data:
        return base64.urlsafe_b64decode(data)
    return b""


async def sync_email_attachments(email: Email, db: AsyncSession) -> list:
    """On-demand backfill: Fetch email payload from Gmail API and save attachments if missing for existing email."""
    if not email or not email.account or not email.gmail_id:
        return []

    if email.attachments:
        return email.attachments

    account = email.account
    try:
        service = get_gmail_service(account.access_token, account.refresh_token)
        msg = await asyncio.to_thread(
            service.users().messages().get(
                userId="me",
                id=email.gmail_id,
                format="full"
            ).execute
        )
        _, _, _, attachments_data = _extract_body_and_attachments(msg.get("payload", {}))
        
        new_atts = []
        for att in attachments_data:
            if att.get("attachment_id"):
                attachment = Attachment(
                    email_id=email.id,
                    filename=att["filename"],
                    mime_type=att["mime_type"],
                    size=att["size"],
                    gmail_attachment_id=att["attachment_id"]
                )
                db.add(attachment)
                new_atts.append(attachment)

        if new_atts:
            await db.commit()
            await db.refresh(email, attribute_names=["attachments"])
        return email.attachments
    except Exception as e:
        logger.error("Failed to sync attachments for email %s: %s", email.id, e)
        return []


async def sync_all_accounts(db: AsyncSession):
    """Sync emails for all connected accounts"""
    result = await db.execute(select(Account))
    accounts = result.scalars().all()
    
    all_new_emails = []
    for account in accounts:
        new_emails = await sync_account(account.id, db)
        all_new_emails.extend(new_emails)
    
    return all_new_emails

async def mark_email_read(email_id: int, db: AsyncSession):
    """Mark an email as read in Gmail and update local database"""
    result = await db.execute(select(Email).where(Email.id == email_id))
    email = result.scalar_one_or_none()
    
    if not email:
        return False
    
    # Get the account
    account_result = await db.execute(select(Account).where(Account.id == email.account_id))
    account = account_result.scalar_one_or_none()
    
    if not account:
        return False
    
    # Update Gmail
    try:
        service = get_gmail_service(account.access_token, account.refresh_token)
        await asyncio.to_thread(
            service.users().messages().modify(
                userId="me",
                id=email.gmail_id,
                body={"removeLabelIds": ["UNREAD"]}
            ).execute
        )
        
        # Update local database
        email.is_read = True
        await db.commit()
        return True
        
    except Exception as e:
        logger.error("Failed to mark email as read: %s", e)
        return False