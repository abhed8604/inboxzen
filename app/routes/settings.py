from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pathlib import Path

from app.database import get_db
from app.models import Settings, Account
from app.config import OLLAMA_HOST
from app.utils import _toast, _get_llm_ctx
from app.services.llm_triage import DEFAULT_RULES
from app.services.llm_providers import get_provider

router = APIRouter(prefix="/settings", tags=["settings"])

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=templates_dir)


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
    llm_provider = await _get_setting(db, "llm_provider", "ollama")
    selected_model = await _get_setting(db, "ollama_model")
    openai_model = await _get_setting(db, "openai_model", "gpt-4o")
    openrouter_model = await _get_setting(db, "openrouter_model")
    openai_key_set = bool(await _get_setting(db, "openai_api_key"))
    openrouter_key_set = bool(await _get_setting(db, "openrouter_api_key"))
    parallel_requests = await _get_setting(db, "parallel_requests", "5")
    rescan_count = await _get_setting(db, "rescan_count", "50")
    ai_rules = await _get_setting(db, "ai_triage_rules")
    if not ai_rules:
        ai_rules = DEFAULT_RULES
    theme = await _get_setting(db, "theme", "dark")

    ollama_models = []
    try:
        from app.services.llm_providers import OllamaProvider
        op = OllamaProvider()
        ollama_models = await op.list_models()
    except Exception:
        ollama_models = []

    accounts_result = await db.execute(select(Account))
    accounts = accounts_result.scalars().all()

    llm_ctx = await _get_llm_ctx(db)

    return templates.TemplateResponse(
        request,
        "settings_modal.html",
        {
            "llm_provider": llm_provider,
            "selected_model": selected_model,
            "ollama_models": ollama_models,
            "openai_model": openai_model,
            "openrouter_model": openrouter_model,
            "openai_key_set": openai_key_set,
            "openrouter_key_set": openrouter_key_set,
            "parallel_requests": parallel_requests,
            "rescan_count": rescan_count,
            "ai_rules": ai_rules,
            "accounts": accounts,
            "ollama_host": OLLAMA_HOST,
            "theme": theme,
            **llm_ctx,
        }
    )


@router.post("/provider")
async def update_provider(provider: str = Form("ollama"), db: AsyncSession = Depends(get_db)):
    if provider not in ("ollama", "openai", "openrouter"):
        return HTMLResponse('<div class="toast error"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>Invalid provider</div>')
    await _set_setting(db, "llm_provider", provider)
    return HTMLResponse(f'<div class="toast success"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>Provider set to {provider}</div>')


@router.post("/model")
async def update_model(model: str = Form(""), db: AsyncSession = Depends(get_db)):
    provider = await _get_setting(db, "llm_provider", "ollama")
    if provider == "ollama":
        await _set_setting(db, "ollama_model", model)
    elif provider == "openai":
        await _set_setting(db, "openai_model", model)
    elif provider == "openrouter":
        await _set_setting(db, "openrouter_model", model)
    return HTMLResponse(f'<div class="toast success"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>Model updated to {model}</div>')


@router.post("/openai-key")
async def update_openai_key(api_key: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "openai_api_key", api_key)
    return HTMLResponse('<div class="toast success"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>OpenAI API key saved</div>')


@router.post("/openrouter-key")
async def update_openrouter_key(api_key: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "openrouter_api_key", api_key)
    return HTMLResponse('<div class="toast success"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>OpenRouter API key saved</div>')


@router.post("/parallel-requests")
async def update_parallel_requests(count: str = Form("5"), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "parallel_requests", count)
    return HTMLResponse(f'<div class="toast success"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>Parallel requests set to {count}</div>')


@router.get("/api/models/{provider_name}")
async def list_models(provider_name: str, db: AsyncSession = Depends(get_db)):
    provider = await get_provider(db)
    if provider.name != provider_name:
        return JSONResponse({"models": []})

    if hasattr(provider, "list_models_detailed"):
        models = await provider.list_models_detailed()
        return JSONResponse({"models": models, "detailed": True})

    models = await provider.list_models()
    return JSONResponse({"models": models, "detailed": False})


@router.post("/rescan-count")
async def update_rescan_count(count: str = Form("50"), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "rescan_count", count)
    return HTMLResponse(f'<div class="toast success"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>Rescan count updated to {count} emails</div>')


@router.post("/ai-rules")
async def update_ai_rules(rules: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "ai_triage_rules", rules)
    return HTMLResponse('<div class="toast success"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>AI triage rules updated</div>')


@router.post("/theme")
async def update_theme(theme: str = Form("dark"), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "theme", theme)
    resp = HTMLResponse(f'<div class="toast success"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>Theme set to {theme}</div>')
    resp.set_cookie("inboxzen_theme", theme, max_age=365 * 24 * 3600, samesite="lax")
    return resp
