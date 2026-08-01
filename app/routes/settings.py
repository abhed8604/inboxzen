from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Settings, Account
from app.config import OLLAMA_HOST
from app.utils import _toast, _get_llm_ctx
from app.services.llm_triage import DEFAULT_RULES
from app.services.llm_providers import get_provider, OllamaProvider

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
    llm_provider = await _get_setting(db, "llm_provider", "ollama")
    selected_model = await _get_setting(db, "ollama_model")
    openai_model = await _get_setting(db, "openai_model", "gpt-4o")
    openrouter_model = await _get_setting(db, "openrouter_model")
    openai_key_set = bool(await _get_setting(db, "openai_api_key"))
    openrouter_key_set = bool(await _get_setting(db, "openrouter_api_key"))
    parallel_requests = await _get_setting(db, "parallel_requests", "5")
    rescan_count = await _get_setting(db, "rescan_count", "50")
    ai_rules = await _get_setting(db, "ai_triage_rules") or DEFAULT_RULES
    theme = await _get_setting(db, "theme", "dark")

    ollama_models = []
    try:
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
        return _toast("Invalid provider", "error")
    await _set_setting(db, "llm_provider", provider)
    return _toast(f"Provider set to {provider}")


@router.post("/model")
async def update_model(model: str = Form(""), db: AsyncSession = Depends(get_db)):
    provider = await _get_setting(db, "llm_provider", "ollama")
    key_map = {"ollama": "ollama_model", "openai": "openai_model", "openrouter": "openrouter_model"}
    if provider in key_map:
        await _set_setting(db, key_map[provider], model)
    return _toast(f"Model updated to {model}")


@router.post("/openai-key")
async def update_openai_key(api_key: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "openai_api_key", api_key)
    return _toast("OpenAI API key saved")


@router.post("/openrouter-key")
async def update_openrouter_key(api_key: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "openrouter_api_key", api_key)
    return _toast("OpenRouter API key saved")


@router.post("/parallel-requests")
async def update_parallel_requests(count: str = Form("5"), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "parallel_requests", count)
    return _toast(f"Parallel requests set to {count}")


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
    return _toast(f"Rescan count updated to {count} emails")


@router.post("/ai-rules")
async def update_ai_rules(rules: str = Form(""), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "ai_triage_rules", rules)
    return _toast("AI triage rules updated")


@router.post("/theme")
async def update_theme(theme: str = Form("dark"), db: AsyncSession = Depends(get_db)):
    await _set_setting(db, "theme", theme)
    resp = _toast(f"Theme set to {theme}")
    resp.set_cookie("inboxzen_theme", theme, max_age=365 * 24 * 3600, samesite="lax")
    return resp
