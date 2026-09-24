import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db, async_session
from app.models import Account
from app.auth.google_oauth import get_google_auth_url, exchange_code_for_tokens, get_user_email
from app.config import ACCOUNT_COLORS
from app.services.gmail_sync import sync_and_triage

router = APIRouter(prefix="/accounts", tags=["accounts"])
logger = logging.getLogger(__name__)


async def _bg_initial_sync():
    try:
        async with async_session() as bg_db:
            await sync_and_triage(bg_db)
    except Exception as exc:
        logger.error("Background sync after OAuth failed: %s", exc)


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
        email = get_user_email(tokens["access_token"], tokens["refresh_token"])
        
        result = await db.execute(select(Account).where(Account.email == email))
        existing_account = result.scalar_one_or_none()
        
        if existing_account:
            existing_account.access_token = tokens["access_token"]
            existing_account.refresh_token = tokens["refresh_token"]
            existing_account.token_expiry = tokens["token_expiry"]
            account = existing_account
        else:
            result = await db.execute(select(Account))
            accounts = result.scalars().all()
            color = ACCOUNT_COLORS[len(accounts) % len(ACCOUNT_COLORS)]
            display_name = email
            
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
        
        asyncio.create_task(_bg_initial_sync())
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
