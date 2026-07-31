"""Shared utilities for InboxZen routes."""

from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Settings
from app.services.llm_providers import get_provider


def _toast(message: str, kind: str = "success") -> HTMLResponse:
    """Return a toast notification HTML fragment."""
    if kind == "success":
        icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
    else:
        icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
    return HTMLResponse(f'<div class="toast {kind}">{icon}{message}</div>')


_MODEL_DEFAULTS = {
    "ollama": "unsloth/gemma-4-E2B-it-GGUF:IQ4_XS",
    "openai": "gpt-4o",
    "openrouter": "",
}
_MODEL_KEYS = {
    "ollama": "ollama_model",
    "openai": "openai_model",
    "openrouter": "openrouter_model",
}


async def get_selected_model(db: AsyncSession) -> str:
    """Read the active model name from settings based on provider."""
    provider = await get_provider(db)
    key = _MODEL_KEYS.get(provider.name, "")
    if key:
        result = await db.execute(select(Settings).where(Settings.key == key))
        setting = result.scalar_one_or_none()
        if setting and setting.value and setting.value.strip():
            return setting.value
    return _MODEL_DEFAULTS.get(provider.name, "")


async def _get_llm_ctx(db: AsyncSession) -> dict:
    """Return LLM status context for templates."""
    provider = await get_provider(db)
    model = await get_selected_model(db)
    status = await provider.check_status(model)
    return {
        "llm_provider": provider.name,
        "llm_model_name": model,
        "llm_online": status.online,
        "llm_loaded": status.loaded,
    }
