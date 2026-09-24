from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Settings, Account
from app.utils import _toast, _get_llm_ctx
from app.services.llm_triage import DEFAULT_RULES

router = APIRouter(prefix="/settings", tags=["settings"])

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=templates_dir)


async def _get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    result = await db.execute(select(Settings).where(Settings.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else default


async def _set_setting(db: AsyncSession, key: str, value: str):
    await db.merge(Settings(key=key, value=value))
    await db.commit()


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    selected_model = await _get_setting(db, "laya_model", "english")
    rescan_count = await _get_setting(db, "rescan_count", "50")
    ai_rules = await _get_setting(db, "ai_triage_rules") or DEFAULT_RULES
    theme = await _get_setting(db, "theme", "dark")

    laya_models = ["english", "multilingual", "typed-decisions"]

    accounts_result = await db.execute(select(Account))
    accounts = accounts_result.scalars().all()

    llm_ctx = await _get_llm_ctx(db)

    return templates.TemplateResponse(
        request,
        "settings_modal.html",
        {
            "selected_model": selected_model,
            "laya_models": laya_models,
            "rescan_count": rescan_count,
            "ai_rules": ai_rules,
            "accounts": accounts,
            "theme": theme,
            **llm_ctx,
        }
    )


@router.post("/model")
async def update_model(model: str = Form(""), db: AsyncSession = Depends(get_db)):
    from app.services.llm_triage import normalize_laya_model
    model_name = normalize_laya_model(model)
    await _set_setting(db, "laya_model", model_name)
    return _toast(f"Laya model updated to {model_name}")


@router.post("/rescan-count")
async def update_rescan_count(count: str = Form("50"), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "rescan_count", count)
    return _toast(f"Rescan count updated to {count} emails")


@router.post("/ai-rules")
async def update_ai_rules(rules: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "ai_triage_rules", rules)
    return _toast("Triage rules updated")


@router.post("/theme")
async def update_theme(theme: str = Form("dark"), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "theme", theme)
    resp = _toast(f"Theme set to {theme}")
    resp.set_cookie("inboxzen_theme", theme, max_age=365 * 24 * 3600, samesite="lax")
    return resp
