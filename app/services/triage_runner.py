"""
Triage orchestration layer.
Bridge between DB and LLM: picks emails, calls llm_triage, persists results.
Handles batching, progress callbacks, cancel checks.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Email, Settings
from app.utils import get_selected_model
from app.services.llm_triage import (
    TriageResult, TriageUnavailable, TriageParseError, TriageRateLimit,
    scan_email, scan_email_with_retry, load_custom_rules,
)
from app.services.llm_providers import get_provider, LLMProvider
from app.websocket_manager import broadcast

logger = logging.getLogger(__name__)

MAX_SCAN_LIMIT = 500
BATCH_SIZE = 10

_CANCEL = False
_scan_state = {"running": False, "done": 0, "total": 0}


def request_cancel() -> None:
    global _CANCEL
    _CANCEL = True


def reset_cancel() -> None:
    global _CANCEL
    _CANCEL = False


async def _get_parallel_requests(db: AsyncSession) -> int:
    result = await db.execute(select(Settings).where(Settings.key == "parallel_requests"))
    setting = result.scalar_one_or_none()
    try:
        return int(setting.value) if setting and setting.value else 5
    except (ValueError, TypeError):
        return 5


async def pick_emails(db: AsyncSession, *, rescan: bool, limit: int | None) -> list[Email]:
    """
    Pick emails for triage.
    Priority 1: Never-scanned emails (triaged_at IS NULL), newest first.
    If rescan: fill remaining slots with already-scanned emails.
    """
    cap = min(limit or MAX_SCAN_LIMIT, MAX_SCAN_LIMIT)

    never_scanned = (await db.execute(
        select(Email)
        .where(Email.triaged_at.is_(None))
        .order_by(Email.received_at.desc().nullslast())
        .limit(cap)
    )).scalars().all()

    if not rescan:
        return never_scanned

    remaining = cap - len(never_scanned)
    if remaining > 0:
        already_scanned = (await db.execute(
            select(Email)
            .where(Email.triaged_at.isnot(None))
            .order_by(Email.received_at.desc())
            .limit(remaining)
        )).scalars().all()
        return list(never_scanned) + list(already_scanned)

    return list(never_scanned)


def mark_email(email: Email, result: TriageResult | None, model: str, error: str | None = None) -> None:
    """Write triage result or error placeholder to email row."""
    now = datetime.now(timezone.utc)

    if result is not None:
        email.summary = result.summary
        email.relevance_score = result.score
        email.category = result.category
        email.action_required = result.action_required
    elif error:
        email.summary = ""
        email.relevance_score = 0
        email.category = "Uncategorized"
        email.action_required = False

    email.scan_model = model
    email.triaged_at = now


async def _email_to_dict(email: Email) -> dict:
    """Convert Email ORM object to dict for llm_triage."""
    import re
    body = re.sub(r'<[^>]+>', '', (email.body or ""))[:1500]
    return {
        "id": email.id,
        "subject": email.subject or "",
        "sender": email.sender or "",
        "body": body,
    }


async def _triage_sequential(
    emails: list[Email],
    provider: LLMProvider,
    model: str,
    rules: str,
    db: AsyncSession,
    total: int,
    on_progress: Callable | None,
    cancel_check: Callable | None,
) -> int:
    """Sequential triage: one email at a time, with retry and rate-limit backoff."""
    import asyncio
    global _CANCEL
    done = 0
    is_free = ":free" in model
    delay = 15.0 if is_free else 0

    for i in range(0, len(emails), BATCH_SIZE):
        if _CANCEL or (cancel_check and cancel_check()):
            break

        chunk = emails[i:i + BATCH_SIZE]

        for email in chunk:
            if _CANCEL or (cancel_check and cancel_check()):
                break

            email_dict = await _email_to_dict(email)
            try:
                result = await scan_email_with_retry(email_dict, provider, model, rules)
                mark_email(email, result, model)
                done += 1
                _scan_state["done"] = done
                if done == 1:
                    await broadcast({"type": "ollama_status", "status": "loaded"})
                await broadcast({"type": "scan_progress", "done": done, "total": total})
                await broadcast({"type": "email_triaged", "email_id": email.id})
                if on_progress:
                    on_progress(done, total)
            except TriageRateLimit:
                mark_email(email, None, model, error="Rate limited — try again later")
                await db.commit()
                raise
            except TriageUnavailable:
                mark_email(email, None, model, error="LLM unavailable")
                await db.commit()
                raise
            except (TriageParseError, Exception) as e:
                logger.error("Failed to triage email %d: %s", email.id, e)
                mark_email(email, None, model, error=str(e))

            if delay and done < total:
                await asyncio.sleep(delay)

        await db.commit()

    return done


async def _triage_parallel(
    emails: list[Email],
    provider: LLMProvider,
    model: str,
    rules: str,
    db: AsyncSession,
    total: int,
    concurrency: int,
    on_progress: Callable | None,
    cancel_check: Callable | None,
) -> int:
    """Parallel triage for online providers (OpenAI/OpenRouter)."""
    global _CANCEL
    semaphore = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def process_one(email: Email):
        nonlocal done
        if _CANCEL or (cancel_check and cancel_check()):
            return

        async with semaphore:
            email_dict = await _email_to_dict(email)
            try:
                result = await scan_email(email_dict, provider, model, rules)
                async with lock:
                    mark_email(email, result, model)
                    await db.commit()
                    done += 1
                    _scan_state["done"] = done
                    if done == 1:
                        await broadcast({"type": "ollama_status", "status": "loaded"})
                    await broadcast({"type": "scan_progress", "done": done, "total": total})
                    await broadcast({"type": "email_triaged", "email_id": email.id})
                    if on_progress:
                        on_progress(done, total)
            except Exception as e:
                logger.error("Failed to triage email %d: %s", email.id, e)
                async with lock:
                    mark_email(email, None, model, error=str(e))
                    await db.commit()

    await asyncio.gather(*[process_one(e) for e in emails])
    return done


async def run_triage_scan(
    db: AsyncSession,
    *,
    rescan: bool = False,
    limit: int | None = None,
    model: str | None = None,
    provider: LLMProvider | None = None,
    on_progress: Callable | None = None,
    cancel_check: Callable | None = None,
) -> int:
    """
    Main entry point for triage scanning.
    Returns: number of emails successfully triaged, or -1 on provider error.
    """
    global _CANCEL
    _CANCEL = False

    emails = await pick_emails(db, rescan=rescan, limit=limit)
    if not emails:
        return 0

    if provider is None:
        provider = await get_provider(db)
    if model is None:
        model = await get_selected_model(db)

    if not model or not model.strip():
        return -1

    rules = load_custom_rules()
    total = len(emails)

    _scan_state["running"] = True
    _scan_state["done"] = 0
    _scan_state["total"] = total
    await broadcast({"type": "scan_start", "total": total, "mode": "rescan" if rescan else "scan"})

    try:
        if provider.supports_parallel():
            concurrency = 1 if ":free" in model else await _get_parallel_requests(db)
            done = await _triage_parallel(
                emails, provider, model, rules, db, total, concurrency,
                on_progress, cancel_check,
            )
        else:
            done = await _triage_sequential(
                emails, provider, model, rules, db, total,
                on_progress, cancel_check,
            )
    finally:
        _scan_state["running"] = False
        _scan_state["done"] = 0
        _scan_state["total"] = 0
        await broadcast({"type": "scan_end"})

    if done == 0 and total > 0:
        return -1

    return done
