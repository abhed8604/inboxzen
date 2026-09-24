"""
Laya decision model triage layer.
No DB access, no WebSocket broadcasts.
Handles: running Laya ModernBERT questions and scoring classifications.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_RULES = """- Job offers, interviews, recruiter messages, test links, and internship results -> critical priority (score 95)
- Final year project/thesis submissions, exam schedules, professor emails, and graduation deadlines -> critical priority (score 90)
- University announcements, departmental notices, and placement cell updates -> high priority (score 85)
- Invoices, tuition fee receipts, scholarships, loan updates, and bank alerts -> high priority (score 80)
- GitHub, coding platforms (LeetCode, HackerRank), and hackathons -> high priority (score 75)
- Campus events, student clubs, workshops, and society announcements -> normal priority (score 45)
- Student discounts, retail newsletters, and promotional sales -> low priority (score 25)
- Marketing pitches and promotional spam -> low priority (score 15)"""


def get_laya_questions(instructions: str = "") -> dict:
    """Build Laya question definitions, incorporating user custom priority rules."""
    urgency_instr = "How urgent or important is this email?"
    if instructions and instructions.strip():
        urgency_instr += f"\nFollow these priority rules:\n{instructions.strip()}"

    cat_instr = "Which category best describes this email in `body`?"
    if instructions and instructions.strip():
        cat_instr += f"\nContext rules:\n{instructions.strip()}"

    return {
        "category": {
            "type": "choice",
            "instructions": cat_instr,
            "criteria": {
                "Careers": "job applications, recruiter messages, interview invitations, hiring offers, internships, coding assessments, placement cell notices",
                "Academics": "professors, course materials, exam schedules, assignment deadlines, final year capstone project, thesis, graduation clearance, grades",
                "Campus": "student clubs, hackathons, college fests, workshops, campus seminars, student society announcements",
                "Finance": "tuition fees, fee receipts, scholarships, student loan updates, stipend deposits, bank alerts",
                "Security": "account security alerts, password resets, 2FA verification codes, student portal access warnings",
                "Personal": "direct personal emails from friends, family, classmates, peer study groups",
                "Promotions": "student discounts, retail sales, marketing emails, product newsletters, commercial pitches",
                "Other": "general communications not matching other categories",
            },
        },
        "urgency": {
            "type": "score",
            "instructions": urgency_instr,
            "criteria": [
                "low priority / newsletter / promotion / FYI only",
                "normal priority, review when convenient",
                "high priority, requires response or attention within 24 hours",
                "critical priority, urgent deadline, security alert, or blocking issue",
            ],
        },
    }


@dataclass
class TriageResult:
    important: bool
    score: int
    reason: str
    category: str
    summary: str = ""


class TriageUnavailable(Exception):
    pass


def score_from_laya_answers(answers: dict) -> tuple[int, bool, str]:
    cat_ans = answers.get("category", {})
    category = cat_ans.get("choice", "Other")
    if isinstance(category, str):
        category = category.title()

    urg_ans = answers.get("urgency", {})
    probs = urg_ans.get("probabilities", {})

    if isinstance(probs, dict) and probs:
        tier_weights = {"0": 15, "1": 45, "2": 75, "3": 95}
        raw_score = sum(float(prob) * tier_weights.get(str(k), 50) for k, prob in probs.items())
    elif "score" in urg_ans:
        try:
            s = float(urg_ans["score"])
            raw_score = (s / 3.0) * 100.0
        except (ValueError, TypeError):
            raw_score = 50.0
    else:
        raw_score = 50.0

    score = int(round(max(0, min(100, raw_score))))
    important = score >= 70
    return score, important, category


def normalize_laya_model(model: str | None) -> str:
    """Map any user or DB model string to a valid Laya router checkpoint."""
    if not model:
        return "english"
    m = model.strip().lower()
    if any(k in m for k in ["multi", "ml"]):
        return "multilingual"
    if "decision" in m:
        return "typed-decisions"
    return "english"


def _predict_laya_sync(state: dict, model: str | None = None, instructions: str = "") -> TriageResult:
    from app.services.llm_providers import get_laya_router
    router = get_laya_router(preload=False)
    if router is None:
        raise TriageUnavailable("Laya router is not initialized or failed to load")

    laya_model = normalize_laya_model(model)

    # Evict other checkpoints to prevent RAM from accumulating multiple models
    for loaded_name in list(router.loaded):
        if loaded_name != laya_model:
            router.unload(loaded_name)

    questions = get_laya_questions(instructions)

    try:
        res = router.predict(state, questions, model=laya_model)
    except Exception as exc:
        raise TriageUnavailable(f"Laya prediction failed: {exc}") from exc

    answers = res.get("answers", {})
    score, important, category = score_from_laya_answers(answers)

    return TriageResult(
        important=important,
        score=score,
        reason="",
        category=category,
        summary="",
    )


async def scan_email(email: dict, model: str | None = None, instructions: str = "") -> TriageResult:
    """Triage email using local non-autoregressive Laya model."""
    subject = email.get("subject") or ""
    sender = email.get("sender") or email.get("sender_name") or email.get("sender_email") or ""
    body = (email.get("body") or email.get("body_text") or "")[:2000]

    state = {
        "subject": subject,
        "from": sender,
        "body": body,
    }
    return await asyncio.to_thread(_predict_laya_sync, state, model, instructions)
