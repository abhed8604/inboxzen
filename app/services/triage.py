import ollama
import json
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Email, Settings
from app.services.triage_prompt import TRIAGE_PROMPT
from app.config import OLLAMA_HOST
from app.websocket_manager import broadcast

async def get_selected_model(db: AsyncSession) -> str:
    """Get the selected Ollama model from Settings table"""
    result = await db.execute(select(Settings).where(Settings.key == "ollama_model"))
    setting = result.scalar_one_or_none()
    
    if setting:
        return setting.value
    
    # Default model if none configured
    return "llama3.2"

async def triage_email(email_id: int, db: AsyncSession):
    """Triage a single email using Ollama"""
    # Get the email
    result = await db.execute(select(Email).where(Email.id == email_id))
    email = result.scalar_one_or_none()
    
    if not email:
        return None
    
    # Get selected model
    model = await get_selected_model(db)
    
    # Prepare the prompt
    prompt = TRIAGE_PROMPT.format(
        sender=email.sender,
        subject=email.subject,
        snippet=email.snippet or "",
        body=email.body or ""
    )
    
    try:
        # Call Ollama
        response = ollama.generate(
            model=model,
            prompt=prompt,
            options={"temperature": 0.3}  # Low temperature for consistent results
        )
        
        # Parse the response - newer ollama versions return an object with .response attribute
        response_text = response.response.strip() if hasattr(response, 'response') else response["response"].strip()
        
        # Remove markdown code fences if present
        if response_text.startswith("```"):
            response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()
        
        # Parse JSON
        triage_result = json.loads(response_text)
        
        # Validate required fields
        required_fields = ["summary", "priority", "category"]
        for field in required_fields:
            if field not in triage_result:
                raise ValueError(f"Missing required field: {field}")
        
        # Validate priority tier
        valid_priorities = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        if triage_result["priority"] not in valid_priorities:
            triage_result["priority"] = "MEDIUM"
        
        # Update email with triage results
        email.summary = triage_result["summary"]
        email.priority = triage_result["priority"]
        email.category = triage_result["category"]
        email.triaged_at = datetime.utcnow()
        
        await db.commit()
        await db.refresh(email)
        
        # Broadcast the triaged email via WebSocket
        await broadcast({
            "type": "email_triaged",
            "email_id": email.id,
            "html": f"Email {email.id} triaged with priority {email.priority}"
        })
        
        return triage_result
        
    except json.JSONDecodeError as e:
        print(f"Failed to parse Ollama response as JSON: {e}")
        print(f"Response text: {response_text}")
        
        # Fallback triage
        email.summary = "Triage failed - manual review needed"
        email.priority = "MEDIUM"
        email.category = "Uncategorized"
        email.triaged_at = datetime.utcnow()
        
        await db.commit()
        return {"summary": email.summary, "priority": email.priority, "category": email.category}
        
    except Exception as e:
        print(f"Triage failed for email {email_id}: {e}")
        
        # Fallback triage
        email.summary = "Triage failed - manual review needed"
        email.priority = "MEDIUM"
        email.category = "Uncategorized"
        email.triaged_at = datetime.utcnow()
        
        await db.commit()
        return {"summary": email.summary, "priority": email.priority, "category": email.category}

async def triage_new_emails(db: AsyncSession):
    """Triage all untriaged emails"""
    result = await db.execute(
        select(Email).where(Email.triaged_at.is_(None))
    )
    untriaged_emails = result.scalars().all()
    
    for email in untriaged_emails:
        await triage_email(email.id, db)
    
    return len(untriaged_emails)