import ollama
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Email, Settings
from app.services.triage_prompt import build_batch_triage_prompt
from app.websocket_manager import broadcast

BATCH_SIZE = 5
DEFAULT_SCORE = 50


async def get_selected_model(db: AsyncSession) -> str:
    result = await db.execute(select(Settings).where(Settings.key == "ollama_model"))
    setting = result.scalar_one_or_none()
    if setting:
        return setting.value
    return "qwen2.5:3b"


async def _get_custom_rules(db: AsyncSession) -> str:
    result = await db.execute(select(Settings).where(Settings.key == "ai_triage_rules"))
    setting = result.scalar_one_or_none()
    return setting.value if setting else ""


def _clamp_score(score) -> int:
    try:
        s = int(score)
    except (TypeError, ValueError):
        return DEFAULT_SCORE
    return max(0, min(100, s))


async def triage_batch(emails: list, model: str, custom_rules: str) -> list[dict]:
    """Triage a batch of emails in a single Ollama call"""
    email_data = [
        {
            "id": e.id,
            "sender": e.sender,
            "subject": e.subject,
            "snippet": e.snippet or "",
            "body": (e.body or "")[:500],
        }
        for e in emails
    ]

    prompt = build_batch_triage_prompt(email_data, custom_rules)

    try:
        response = ollama.generate(
            model=model,
            prompt=prompt,
            options={"temperature": 0.3}
        )

        response_text = response.response.strip() if hasattr(response, 'response') else response["response"].strip()

        if response_text.startswith("```"):
            response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        results = json.loads(response_text)

        if not isinstance(results, list):
            raise ValueError("Expected JSON array from batch triage")

        return results

    except (json.JSONDecodeError, Exception) as e:
        print(f"Batch triage failed: {e}")
        return [
            {
                "email_id": e.id,
                "summary": "Triage failed - manual review needed",
                "relevance_score": DEFAULT_SCORE,
                "category": "Uncategorized",
            }
            for e in emails
        ]


async def triage_new_emails(db: AsyncSession):
    """Triage all untriaged emails in batches of 5"""
    result = await db.execute(
        select(Email).where(Email.triaged_at.is_(None))
    )
    untriaged_emails = result.scalars().all()

    if not untriaged_emails:
        return 0

    model = await get_selected_model(db)
    custom_rules = await _get_custom_rules(db)
    now = datetime.now(timezone.utc)

    for i in range(0, len(untriaged_emails), BATCH_SIZE):
        batch = untriaged_emails[i : i + BATCH_SIZE]
        batch_results = await triage_batch(batch, model, custom_rules)

        id_to_email = {e.id: e for e in batch}

        for result in batch_results:
            eid = result.get("email_id")
            email = id_to_email.get(eid)
            if not email:
                continue

            email.summary = result.get("summary", "No summary")
            email.relevance_score = _clamp_score(result.get("relevance_score", DEFAULT_SCORE))
            email.category = result.get("category", "Uncategorized")
            email.triaged_at = now

        await db.commit()

        for email in batch:
            await broadcast({
                "type": "email_triaged",
                "email_id": email.id,
                "html": f"Email {email.id} scored {email.relevance_score}"
            })

    return len(untriaged_emails)
