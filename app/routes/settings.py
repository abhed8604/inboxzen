from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pathlib import Path
import httpx

from app.database import get_db
from app.models import Settings, Account, Email
from app.config import OLLAMA_HOST, DEFAULT_POLL_INTERVAL

router = APIRouter(prefix="/settings", tags=["settings"])

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=templates_dir)

@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Settings page rendered in detail panel"""
    model_result = await db.execute(select(Settings).where(Settings.key == "ollama_model"))
    model_setting = model_result.scalar_one_or_none()
    selected_model = model_setting.value if model_setting else ""

    interval_result = await db.execute(select(Settings).where(Settings.key == "poll_interval_minutes"))
    interval_setting = interval_result.scalar_one_or_none()
    poll_interval = interval_setting.value if interval_setting else str(DEFAULT_POLL_INTERVAL)

    available_models = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                available_models = [model["name"] for model in data.get("models", [])]
    except Exception as e:
        print(f"Failed to fetch Ollama models: {e}")
        available_models = ["llama3.2", "mistral", "phi3", "gemma2"]

    accounts_result = await db.execute(select(Account))
    accounts = accounts_result.scalars().all()

    all_emails_result = await db.execute(select(Email).where(Email.is_archived == False))
    all_emails = all_emails_result.scalars().all()
    unread_count = sum(1 for e in all_emails if not e.is_read)

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "selected_model": selected_model,
            "available_models": available_models,
            "poll_interval": poll_interval,
            "accounts": accounts,
            "ollama_host": OLLAMA_HOST,
            "unread_count": unread_count,
            "total_count": len(all_emails),
        }
    )

@router.post("/model")
async def update_model(model: str = "", db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Settings).where(Settings.key == "ollama_model"))
    existing = result.scalar_one_or_none()

    if existing:
        existing.value = model
    else:
        new_setting = Settings(key="ollama_model", value=model)
        db.add(new_setting)

    await db.commit()
    return {"message": f"Model updated to {model}"}

@router.post("/poll-interval")
async def update_poll_interval(interval: str = "5", db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Settings).where(Settings.key == "poll_interval_minutes"))
    existing = result.scalar_one_or_none()

    if existing:
        existing.value = interval
    else:
        new_setting = Settings(key="poll_interval_minutes", value=interval)
        db.add(new_setting)

    await db.commit()

    from app.services.scheduler import update_scheduler_interval
    try:
        update_scheduler_interval(int(interval))
    except Exception as e:
        print(f"Failed to update scheduler interval: {e}")

    return {"message": f"Poll interval updated to {interval} minutes"}
