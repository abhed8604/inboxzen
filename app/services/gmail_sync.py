from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
import re
import base64

from app.models import Account, Email
from app.auth.google_oauth import get_gmail_service, refresh_access_token


def _clean_body(raw: str) -> str:
    """Strip tracking URLs, redirect links, and tracking pixels from email body."""
    if not raw:
        return ""

    # Remove HTML tags but keep line breaks for structure
    text = re.sub(r'<br\s*/?>', '\n', raw, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)

    # Remove tracking URL patterns (utm_*, redirect links, tracking domains)
    tracking_domains = [
        r'myunidays\.com', r'bloomreach\.', r'link\.linkedin\.com',
        r'click\.mail\.', r'click\.google\.', r'tracking\.', r'pixel\.',
        r'open\.tracking\.', r'emails\.', r'e\.corp\.', r'rd\.google\.com',
        r'discover\.apple\.com', r'ba\.link', r'mkt\.', r'go\.appsflyer\.com',
        r't\.co', r'bit\.ly', r'goo\.gl', r'tinyurl\.com',
    ]

    # Remove entire URLs matching tracking domains
    for domain in tracking_domains:
        text = re.sub(r'https?://[^\s<>"]*' + domain + r'[^\s<>"]*', '', text, flags=re.IGNORECASE)

    # Remove utm_* parameters from remaining URLs
    text = re.sub(r'[?&](utm_\w+=[^&\s]*)', '', text)

    # Remove tracking image/pixel lines (lines that are just URLs)
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip lines that are just URLs or tracking nonsense
        if re.match(r'^https?://\S+$', stripped):
            continue
        # Skip empty bracket lines like [https://...]
        if re.match(r'^\[https?://', stripped):
            continue
        cleaned.append(line)

    text = '\n'.join(cleaned)

    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()

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
                print(f"Failed to refresh token for account {account.email}: {e}")
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
                except:
                    pass
            
            # Get snippet and body
            snippet = msg.get("snippet", "")
            body = ""
            if "parts" in msg["payload"]:
                for part in msg["payload"]["parts"]:
                    if part["mimeType"] == "text/plain":
                        body = part.get("body", {}).get("data", "")
                        if body:
                            body = base64.urlsafe_b64decode(body).decode("utf-8", errors="ignore")
                        break
            elif msg["payload"]["mimeType"] == "text/plain":
                body = msg["payload"].get("body", {}).get("data", "")
                if body:
                    body = base64.urlsafe_b64decode(body).decode("utf-8", errors="ignore")

            body = _clean_body(body)[:2000]
            
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
        print(f"Failed to sync account {account.email}: {e}")
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
        print(f"Failed to mark email as read: {e}")
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
        print(f"Failed to trash email: {e}")
        return False