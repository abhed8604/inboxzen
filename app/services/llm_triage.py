"""
Pure LLM integration layer.
No DB access, no WebSocket broadcasts.
Handles: prompt construction, provider HTTP calls, JSON extraction, result coercion.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.llm_providers import LLMProvider, OllamaProvider

logger = logging.getLogger(__name__)

UNAVAILABLE_RETRY_SECONDS = 30

DEFAULT_RULES = """## Default Scoring Rules

- Emails from **@github.com** (notifications, PRs, issues) → 80
- Emails with subject containing **invoice** or **payment** → 90
- Emails with subject containing **job offer** or **interview** → 95
- Emails from your manager or team lead → 90
- Bank alerts or security notifications → 95
- Meeting invitations or calendar events → 80
- Newsletters from **substack.com**, **medium.com**, or tech blogs → 40
- Marketing emails or promotions → 20
- Automated notifications (CI/CD, deploys, monitoring) → 30
- Social media notifications (LinkedIn, Twitter) → 25
- Personal messages from known contacts → 75
- Emails requiring a response within 24 hours → 85
- Emails that are FYI only with no action needed → 35"""

RULES_PATH = Path.home() / ".inboxzen" / "triage_rules.md"

PROMPT_TEMPLATE = """{rules}

------------------------
EMAIL TO ANALYZE
------------------------

Subject:
{subject}

From:
{sender}

To:
{to}

Body:
{body}

Return ONLY a JSON object with exactly these fields, no additional text:
{{"importance":"CRITICAL|HIGH|MEDIUM|LOW","score":<0-100>,"category":"<category>","action_required":<true|false>,"reason":"<one sentence>","summary":"<1-2 sentences>"}}"""


@dataclass
class TriageResult:
    important: bool
    score: int
    reason: str
    category: str
    summary: str = ""
    action_required: bool = False

    def to_dict(self) -> dict:
        return {
            "important": self.important,
            "score": self.score,
            "reason": self.reason,
            "category": self.category,
            "summary": self.summary,
            "action_required": self.action_required,
        }


class TriageParseError(Exception):
    pass


class TriageUnavailable(Exception):
    pass


class TriageRateLimit(Exception):
    """Raised when the LLM provider returns 429 Too Many Requests."""
    pass


def load_custom_rules() -> str:
    """Load custom triage rules from ~/.inboxzen/triage_rules.md"""
    if RULES_PATH.exists():
        try:
            return RULES_PATH.read_text().strip()
        except Exception:
            pass
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        RULES_PATH.write_text(DEFAULT_RULES)
    except Exception:
        pass
    return DEFAULT_RULES


def build_prompt(email: dict, rules: str) -> str:
    """Build prompt for single email triage."""
    body = (email.get("body") or email.get("body_text") or "")[:1500]
    sender = email.get("sender") or email.get("sender_name") or email.get("sender_email") or "Unknown"
    subject = email.get("subject") or "(no subject)"
    to_addr = email.get("to") or email.get("recipient_email") or "me"

    return PROMPT_TEMPLATE.format(
        rules=rules,
        subject=subject,
        sender=sender,
        to=to_addr,
        body=body,
    )


def extract_json(raw: str) -> dict:
    """Extract JSON from LLM output, tolerating markdown fences and prose."""
    if raw is None:
        raise TriageParseError("empty response")

    text = raw.strip()

    m = re.match(r'^\s*```(?:json)?\s*(.*?)\s*```\s*$', text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            for trim in range(1, 20):
                try:
                    return json.loads(candidate[:-trim])
                except json.JSONDecodeError:
                    continue

    raise TriageParseError(f"Cannot extract JSON from: {raw[:200]}")


def coerce_result(parsed: dict) -> TriageResult:
    """Validate and normalize parsed dict to TriageResult."""
    try:
        score = int(round(float(parsed.get("score") or parsed.get("importance_score", 0))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))

    category = str(parsed.get("category") or "Other").strip().title() or "Other"

    importance = str(parsed.get("importance", "")).strip().lower()
    important = importance in {"critical", "high"} or score >= 70

    action_raw = parsed.get("action_required", False)
    if isinstance(action_raw, str):
        action_required = action_raw.lower() in {"true", "1", "yes"}
    else:
        action_required = bool(action_raw)

    reason = str(parsed.get("reason") or "No reason provided.").strip()[:500]
    summary = str(parsed.get("summary") or "").strip()[:2000]

    return TriageResult(
        important=important,
        score=score,
        reason=reason,
        category=category,
        summary=summary,
        action_required=action_required,
    )


async def scan_email(email: dict, provider: LLMProvider, model: str, rules: str) -> TriageResult:
    """Scan a single email through the LLM. Async version for API providers."""
    prompt = build_prompt(email, rules)
    try:
        raw = await provider.generate(prompt, model)
    except Exception as exc:
        if "429" in str(exc):
            raise TriageRateLimit(f"Rate limited by {provider.name}") from exc
        raise TriageUnavailable(f"LLM request failed: {exc}") from exc
    parsed = extract_json(raw)
    return coerce_result(parsed)


async def scan_email_with_retry(email: dict, provider: LLMProvider, model: str, rules: str) -> TriageResult:
    """Scan with retry: Ollama waits 30s on unavailable, rate-limited providers retry once."""
    import asyncio
    try:
        return await scan_email(email, provider, model, rules)
    except TriageRateLimit:
        logger.info("Rate limited by %s, waiting 60s for retry...", provider.name)
        await asyncio.sleep(60)
        return await scan_email(email, provider, model, rules)
    except TriageUnavailable:
        if provider.name == "ollama":
            logger.info("Ollama unavailable, retrying in %ds...", UNAVAILABLE_RETRY_SECONDS)
            await asyncio.sleep(UNAVAILABLE_RETRY_SECONDS)
            return await scan_email(email, provider, model, rules)
        raise
