"""Shared utilities for InboxZen routes."""

from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Settings
from app.services.llm_providers import is_laya_loaded


def _toast(message: str, kind: str = "success") -> HTMLResponse:
    """Return a toast notification HTML fragment."""
    if kind == "success":
        icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
    elif kind == "warning":
        icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
    elif kind == "info":
        icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
    else:
        icon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
    return HTMLResponse(f'<div class="toast {kind}">{icon}{message}</div>')


async def get_selected_model(db: AsyncSession) -> str:
    """Read the active Laya model name from settings."""
    result = await db.execute(select(Settings).where(Settings.key == "laya_model"))
    setting = result.scalar_one_or_none()
    val = setting.value.strip() if setting and setting.value else "english"
    from app.services.llm_triage import normalize_laya_model
    return normalize_laya_model(val)


async def _get_llm_ctx(db: AsyncSession) -> dict:
    """Return LLM status context for templates."""
    model = await get_selected_model(db)
    return {
        "llm_provider": "laya",
        "llm_model_name": model,
        "llm_online": True,
        "llm_loaded": is_laya_loaded(),
    }
