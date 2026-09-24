from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Email, Settings
from app.utils import get_selected_model
from app.services.llm_triage import TriageResult, scan_email, DEFAULT_RULES
from app.websocket_manager import broadcast

logger = logging.getLogger(__name__)

MAX_SCAN_LIMIT = 500
CONCURRENCY = 4

_CANCEL = False
_scan_state = {"running": False, "done": 0, "total": 0}


def request_cancel() -> None:
    global _CANCEL
    _CANCEL = True


async def pick_emails(db: AsyncSession, *, rescan: bool, limit: int | None) -> list[Email]:
    """
    Pick emails for triage.
    Priority 1: Never-scanned emails (triaged_at IS NULL), newest first.
    If rescan: fill remaining slots with already-scanned emails.
    """
    cap = min(limit or MAX_SCAN_LIMIT, MAX_SCAN_LIMIT)

    never_scanned = list((await db.execute(
        select(Email)
        .where(Email.triaged_at.is_(None))
        .order_by(Email.received_at.desc().nullslast())
        .limit(cap)
    )).scalars().all())

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
        return never_scanned + list(already_scanned)

    return never_scanned


def mark_email(email: Email, result: TriageResult | None, model: str, error: str | None = None) -> None:
    """Write triage result or error placeholder to email row."""
    now = datetime.now(timezone.utc)

    if result is not None:
        email.summary = result.summary
        email.relevance_score = result.score
        email.category = result.category
    elif error:
        email.summary = ""
        email.relevance_score = 0
        email.category = "Uncategorized"

    email.scan_model = model
    email.triaged_at = now


def _email_to_dict(email: Email) -> dict:
    """Convert Email ORM object to dict with clean plain text body for llm_triage."""
    raw_body = email.body or ""
    clean_text = re.sub(r'<(style|script|head|svg)[^>]*>.*?</\1>', '', raw_body, flags=re.DOTALL | re.IGNORECASE)
    clean_text = re.sub(r'<[^>]+>', ' ', clean_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()[:1500]

    return {
        "id": email.id,
        "subject": email.subject or "",
        "sender": email.sender or "",
        "body": clean_text,
    }


async def _notify_progress(done: int, total: int, email_id: int, on_progress: Callable | None = None) -> None:
    _scan_state["done"] = done
    if done == 1:
        await broadcast({"type": "llm_status", "status": "loaded"})
    await broadcast({"type": "scan_progress", "done": done, "total": total})
    await broadcast({"type": "email_triaged", "email_id": email_id})
    if on_progress:
        on_progress(done, total)


async def _triage_parallel(
    emails: list[Email],
    model: str,
    db: AsyncSession,
    total: int,
    concurrency: int = CONCURRENCY,
    instructions: str = "",
    on_progress: Callable | None = None,
    cancel_check: Callable | None = None,
) -> int:
    """Parallel triage across multiple emails."""
    global _CANCEL
    semaphore = asyncio.Semaphore(concurrency)
    done = 0
    lock = asyncio.Lock()

    async def process_one(email: Email):
        nonlocal done
        if _CANCEL or (cancel_check and cancel_check()):
            return

        async with semaphore:
            email_dict = _email_to_dict(email)
            try:
                result = await scan_email(email_dict, model=model, instructions=instructions)
                async with lock:
                    mark_email(email, result, model)
                    await db.commit()
                    done += 1
                    await _notify_progress(done, total, email.id, on_progress)
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
    instructions: str | None = None,
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

    if model is None:
        model = await get_selected_model(db)

    if not model or not model.strip():
        return -1

    if instructions is None:
        rules_res = await db.execute(select(Settings).where(Settings.key == "ai_triage_rules"))
        rules_setting = rules_res.scalar_one_or_none()
        instructions = rules_setting.value if rules_setting and rules_setting.value else DEFAULT_RULES

    total = len(emails)

    _scan_state["running"] = True
    _scan_state["done"] = 0
    _scan_state["total"] = total
    await broadcast({"type": "scan_start", "total": total, "mode": "rescan" if rescan else "scan"})

    try:
        done = await _triage_parallel(
            emails, model, db, total,
            instructions=instructions,
            on_progress=on_progress, cancel_check=cancel_check,
        )
    finally:
        _scan_state["running"] = False
        _scan_state["done"] = 0
        _scan_state["total"] = 0
        await broadcast({"type": "scan_end"})
        import gc
        gc.collect()

    if done == 0 and total > 0:
        return -1

    return done
