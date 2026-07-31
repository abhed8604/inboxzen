from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
import logging
import re
import base64
import html as html_lib

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
    # NOTE: <head> is NOT stripped — it contains <style> blocks emails need for layout
    text = re.sub(
        r'<(script|iframe|object|embed|form|input|textarea|button|svg)[^>]*>.*?</\1>',
        '', raw, flags=re.IGNORECASE | re.DOTALL,
    )
    # Remove self-closing dangerous tags
    text = re.sub(r'<(script|iframe|object|embed|form|input|textarea|button|svg|meta|link)[^>]*/?\s*>', '', text, flags=re.IGNORECASE)
    # Remove <head> wrapper but KEEP its content (style blocks, title, etc.)
    text = re.sub(r'<head[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'</head>', '', text, flags=re.IGNORECASE)
    # Remove event handlers (onclick, onerror, onload, etc.)
    text = re.sub(r'\s*on\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|\S+)', '', text, flags=re.IGNORECASE)
    # Remove javascript: URLs
    text = re.sub(r'href\s*=\s*(?:"javascript:[^"]*"|\'javascript:[^\']*\')', '', text, flags=re.IGNORECASE)
    # Strip tags not in whitelist (keep their content)
    def _tag_strip(m):
        tag = m.group(0)
        name = re.match(r'</?([a-zA-Z]+)', tag)
        if name and name.group(1).lower() in _SAFE_TAGS:
            return tag
        return ''
    text = re.sub(r'<[^>]+>', _tag_strip, text)
    # Clean tracking domains from href attributes
    for domain in _TRACKING_DOMAINS:
        text = re.sub(
            r'href\s*=\s*("https?://[^"]*' + domain + r'[^"]*"|\'https?://[^\']*' + domain + r'[^\']*\')',
            '', text, flags=re.IGNORECASE,
        )
    # Remove utm_* query params from remaining URLs
    text = re.sub(r'(\?|&)(utm_\w+=[^&"\']*)&?', lambda m: '?' if m.group(0).startswith('?') else '', text)
    text = re.sub(r'\?$', '', text)
    # Collapse excessive whitespace but preserve intentional breaks
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _plain_to_html(text: str) -> str:
    """Convert plain text to simple HTML with auto-linked URLs."""
    if not text:
        return ""
    text = html_lib.escape(text)
    text = re.sub(r'(https?://[^\s<>"]+)', r'<a href="\1" target="_blank" rel="noopener">\1</a>', text)
    text = text.replace('\n', '<br>')
    return text


def _resolve_cid_refs(html: str, images: dict) -> str:
    """Replace cid: references in HTML with data: URIs from collected image parts."""
    if not images:
        return html

    def replace_cid(m):
        cid = m.group(1)
        if cid in images:
            mime, data = images[cid]
            return f'src="data:{mime};base64,{data}"'
        return m.group(0)

    return re.sub(r'src\s*=\s*["\']cid:([^"\']+)["\']', replace_cid, html, flags=re.IGNORECASE)


_PROXY_img_PATTERN = re.compile(
    r'(<img\s[^>]*?)src\s*=\s*["\']?(https?://[^"\'>\s]+)["\']?',
    re.IGNORECASE,
)


def _rewrite_image_urls(html: str) -> str:
    """Rewrite external <img src="https://..."> to /proxy/image?url=... so browser can load them."""
    from urllib.parse import quote

    def _replace(m):
        prefix = m.group(1)
        url = m.group(2)
        return f'{prefix}src="/proxy/image?url={quote(url, safe="")}"'

    return _PROXY_img_PATTERN.sub(_replace, html)


def _extract_body(payload: dict) -> tuple[str, str, dict]:
    """Extract HTML, plain text bodies, and inline images from Gmail payload.
    Returns (html, plain, images_dict) where images_dict maps content_id -> (mime, base64_data).
    """
    html_body = ""
    plain_body = ""
    images = {}

    parts = payload.get("parts", [])
    if not parts:
        # Single-part message
        mime = payload.get("mimeType", "")
        data = payload.get("body", {}).get("data", "")
        if data:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            if mime == "text/html":
                html_body = decoded
            elif mime == "text/plain":
                plain_body = decoded
        return html_body, plain_body, images

    # Multipart — recurse into parts
    for part in parts:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")

        if mime == "text/html" and data and not html_body:
            html_body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        elif mime == "text/plain" and data and not plain_body:
            plain_body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        elif mime.startswith("image/") and data:
            # Collect inline image — find its Content-Id header
            cid = ""
            for h in part.get("headers", []):
                if h["name"].lower() == "content-id":
                    cid = h["value"].strip("<>")
                    break
            if cid:
                images[cid] = (mime, data)

        # Recurse into nested parts (e.g. multipart/alternative inside multipart/mixed)
        if "parts" in part:
            sub_html, sub_plain, sub_images = _extract_body(part)
            if sub_html and not html_body:
                html_body = sub_html
            if sub_plain and not plain_body:
                plain_body = sub_plain
            images.update(sub_images)

    return html_body, plain_body, images

async def sync_account(account_id: int, db: AsyncSession):
    """Sync emails for a specific Gmail account"""
    # Get the account
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        return []
    
    # Check if token is expired and refresh if needed
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
    
    # Get Gmail service
    service = get_gmail_service(account.access_token, account.refresh_token)
    
    # Fetch recent emails (last 50 for MVP)
    try:
        results = service.users().messages().list(
            userId="me",
            maxResults=50,
            labelIds=["INBOX"]
        ).execute()
        
        messages = results.get("messages", [])
        new_emails = []
        
        for message in messages:
            gmail_id = message["id"]
            
            # Check if email already exists
            result = await db.execute(
                select(Email).where(
                    Email.gmail_id == gmail_id,
                    Email.account_id == account_id
                )
            )
            existing_email = result.scalar_one_or_none()
            
            if existing_email:
                continue
            
            # Get full email details
            msg = service.users().messages().get(
                userId="me",
                id=gmail_id,
                format="full"
            ).execute()
            
            # Extract email data
            headers = msg["payload"]["headers"]
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "No Subject")
            sender = next((h["value"] for h in headers if h["name"] == "From"), "Unknown Sender")
            date_str = next((h["value"] for h in headers if h["name"] == "Date"), None)
            
            # Parse date
            received_at = datetime.now(timezone.utc)
            if date_str:
                try:
                    # Simple date parsing for MVP
                    from email.utils import parsedate_to_datetime
                    received_at = parsedate_to_datetime(date_str)
                except Exception:
                    pass
            
            # Get snippet and body
            snippet = msg.get("snippet", "")
            html_body, plain_body, images = _extract_body(msg["payload"])

            if html_body:
                body = _sanitize_html(html_body)
                body = _resolve_cid_refs(body, images)
                body = _rewrite_image_urls(body)
            elif plain_body:
                body = _plain_to_html(plain_body)
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
            new_emails.append(email)
        
        await db.commit()
        
        # Return new emails for triage
        return new_emails
        
    except Exception as e:
        logger.error("Failed to sync account %s: %s", account.email, e)
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
        service.users().messages().modify(
            userId="me",
            id=email.gmail_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        
        # Update local database
        email.is_read = True
        await db.commit()
        return True
        
    except Exception as e:
        logger.error("Failed to mark email as read: %s", e)
        return False

async def trash_email(email_id: int, db: AsyncSession):
    """Move an email to trash in Gmail and update local database"""
    result = await db.execute(select(Email).where(Email.id == email_id))
    email = result.scalar_one_or_none()

    if not email:
        return False

    account_result = await db.execute(select(Account).where(Account.id == email.account_id))
    account = account_result.scalar_one_or_none()

    if not account:
        return False

    try:
        service = get_gmail_service(account.access_token, account.refresh_token)
        service.users().messages().modify(
            userId="me",
            id=email.gmail_id,
            body={"removeLabelIds": ["INBOX"], "addLabelIds": ["TRASH"]}
        ).execute()

        email.is_archived = True
        await db.commit()
        return True

    except Exception as e:
        logger.error("Failed to trash email: %s", e)
        return False