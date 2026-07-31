from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Account
from app.auth.google_oauth import get_google_auth_url, exchange_code_for_tokens, get_user_email
from app.config import ACCOUNT_COLORS
from app.services.gmail_sync import sync_all_accounts
from app.services.triage_runner import run_triage_scan

router = APIRouter(prefix="/accounts", tags=["accounts"])

@router.get("/connect")
async def connect_account():
    """Initiate Google OAuth2 flow for connecting a Gmail account"""
    authorization_url, state = get_google_auth_url()
    return RedirectResponse(url=authorization_url)

@router.get("/oauth2callback")
async def oauth2callback(code: str = None, state: str = None, db: AsyncSession = Depends(get_db)):
    """Handle Google OAuth2 callback"""
    if not code or not state:
        raise HTTPException(status_code=400, detail="Authorization code or state not provided")
    
    try:
        tokens = exchange_code_for_tokens(code, state)
        
        # Get user's email address
        email = get_user_email(tokens["access_token"], tokens["refresh_token"])
        
        # Check if account already exists
        result = await db.execute(select(Account).where(Account.email == email))
        existing_account = result.scalar_one_or_none()
        
        if existing_account:
            # Update existing account with new tokens
            existing_account.access_token = tokens["access_token"]
            existing_account.refresh_token = tokens["refresh_token"]
            existing_account.token_expiry = tokens["token_expiry"]
            account = existing_account
        else:
            # Create new account
            # Get next color from palette
            result = await db.execute(select(Account))
            accounts = result.scalars().all()
            color_index = len(accounts) % len(ACCOUNT_COLORS)
            color = ACCOUNT_COLORS[color_index]
            
            # Extract display name from email (part before @)
            display_name = email.split("@")[0].replace(".", " ").title()
            
            account = Account(
                email=email,
                display_name=display_name,
                color=color,
                access_token=tokens["access_token"],
                refresh_token=tokens["refresh_token"],
                token_expiry=tokens["token_expiry"]
            )
            db.add(account)
        
        await db.commit()
        await db.refresh(account)
        
        # Sync emails and triage on first connect
        new_emails = await sync_all_accounts(db)
        if new_emails:
                await run_triage_scan(db)
        
        return RedirectResponse(url="/")
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OAuth2 flow failed: {str(e)}")

@router.delete("/{account_id}")
async def disconnect_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """Disconnect a Gmail account"""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    await db.delete(account)
    await db.commit()
    
    return HTMLResponse("")
