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
from app.services.triage_prompt import DEFAULT_AI_RULES

router = APIRouter(prefix="/settings", tags=["settings"])

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=templates_dir)


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


async def _get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else default


async def _set_setting(db: AsyncSession, key: str, value: str):
    result = await db.execute(select(Settings).where(Settings.key == key))
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        db.add(Settings(key=key, value=value))
    await db.commit()


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    selected_model = await _get_setting(db, "ollama_model")
    poll_interval = await _get_setting(db, "poll_interval_minutes", str(DEFAULT_POLL_INTERVAL))
    ai_rules = await _get_setting(db, "ai_triage_rules")
    if not ai_rules:
        ai_rules = DEFAULT_AI_RULES
    theme = await _get_setting(db, "theme", "dark")

    available_models = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{OLLAMA_HOST}/api/tags", timeout=5.0)
            if response.status_code == 200:
                data = response.json()
                available_models = [m["name"] for m in data.get("models", [])]
    except Exception:
        available_models = ["qwen2.5:3b", "llama3.2", "mistral", "phi3"]

    accounts_result = await db.execute(select(Account))
    accounts = accounts_result.scalars().all()

    all_emails_result = await db.execute(select(Email).where(Email.is_archived == False))
    all_emails = all_emails_result.scalars().all()
    unread_count = sum(1 for e in all_emails if not e.is_read)
    important_count = sum(1 for e in all_emails if (e.relevance_score or 0) >= 70)

    ollama_ctx = await _get_ollama_ctx(db)

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "selected_model": selected_model,
            "available_models": available_models,
            "poll_interval": poll_interval,
            "ai_rules": ai_rules,
            "accounts": accounts,
            "ollama_host": OLLAMA_HOST,
            "unread_count": unread_count,
            "important_count": important_count,
            "total_count": len(all_emails),
            "settings_mode": True,
            "theme": theme,
            **ollama_ctx,
        }
    )


@router.post("/model")
async def update_model(model: str = "", db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "ollama_model", model)
    return HTMLResponse(f'<div class="toast success">Model updated to {model}</div>')


@router.post("/poll-interval")
async def update_poll_interval(interval: str = "5", db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "poll_interval_minutes", interval)

    from app.services.scheduler import update_scheduler_interval
    try:
        update_scheduler_interval(int(interval))
    except Exception as e:
        print(f"Failed to update scheduler interval: {e}")

    return HTMLResponse(f'<div class="toast success">Poll interval updated to {interval} minutes</div>')


@router.post("/ai-rules")
async def update_ai_rules(rules: str = "", db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "ai_triage_rules", rules)
    return HTMLResponse('<div class="toast success">AI triage rules updated</div>')


@router.post("/theme")
async def update_theme(theme: str = "dark", db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "theme", theme)
    resp = HTMLResponse(f'<div class="toast success">Theme set to {theme}</div>')
    resp.set_cookie("inboxzen_theme", theme, max_age=365 * 24 * 3600, samesite="lax")
    return resp
