DEFAULT_AI_RULES = """## Default Scoring Rules

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

BASE_TRIAGE_PROMPT = """You are an email triage assistant. Analyze the following email and provide a relevance score.

Email Details:
- From: {sender}
- Subject: {subject}
- Snippet: {snippet}
- Body: {body}

Please provide a JSON response with exactly these fields:
{{
    "summary": "A concise 1-2 sentence summary of the email",
    "relevance_score": <0-100>,
    "category": "A category label (e.g., Job Offer, Bank/Finance, Newsletter, Personal, Meeting, Invoice, Marketing, Social, Other)"
}}

Relevance Score Guidelines:
- 90-100: Highly relevant — urgent/time-sensitive, job offers, bank/security alerts, critical deadlines
- 70-89: Moderately relevant — meetings, personal messages requiring response, action items
- 40-69: Low-medium relevance — updates, FYI content, general correspondence
- 0-39: Low relevance — newsletters, promotions, automated notifications, spam-ish content

Return ONLY the JSON response, no additional text or markdown formatting."""

BATCH_TRIAGE_PROMPT = """You are an email triage assistant. Analyze each of the following emails and provide a relevance score for ALL of them.

{emails_block}

For each email, provide a JSON object with these fields:
- "email_id": the email ID number provided above
- "summary": A concise 1-2 sentence summary
- "relevance_score": <0-100>
- "category": A category label (e.g., Job Offer, Bank/Finance, Newsletter, Personal, Meeting, Invoice, Marketing, Social, Other)

Relevance Score Guidelines:
- 90-100: Highly relevant — urgent/time-sensitive, job offers, bank/security alerts, critical deadlines
- 70-89: Moderately relevant — meetings, personal messages requiring response, action items
- 40-69: Low-medium relevance — updates, FYI content, general correspondence
- 0-39: Low relevance — newsletters, promotions, automated notifications, spam-ish content

Return a JSON array containing exactly {count} objects, one per email, in the same order. No additional text, no markdown formatting."""


def build_triage_prompt(custom_rules: str = "") -> str:
    prompt = BASE_TRIAGE_PROMPT
    if custom_rules and custom_rules.strip():
        prompt += f"\n\nAdditional Rules (follow these in addition to the guidelines above):\n{custom_rules.strip()}"
    return prompt


def build_batch_triage_prompt(emails: list[dict], custom_rules: str = "") -> str:
    lines = []
    for e in emails:
        lines.append(
            f"Email ID: {e['id']}\n"
            f"- From: {e['sender']}\n"
            f"- Subject: {e['subject']}\n"
            f"- Snippet: {e.get('snippet', '')}\n"
            f"- Body: {e.get('body', '')[:500]}"
        )
    emails_block = "\n\n".join(lines)
    prompt = BATCH_TRIAGE_PROMPT.format(emails_block=emails_block, count=len(emails))
    if custom_rules and custom_rules.strip():
        prompt += f"\n\nAdditional Rules (follow these in addition to the guidelines above):\n{custom_rules.strip()}"
    return prompt
