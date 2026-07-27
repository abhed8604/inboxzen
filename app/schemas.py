from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class AccountBase(BaseModel):
    email: str
    display_name: Optional[str]
    color: str

class Account(AccountBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class EmailBase(BaseModel):
    sender: str
    subject: str
    snippet: Optional[str]
    received_at: datetime
    is_read: bool
    is_archived: bool

class Email(EmailBase):
    id: int
    account_id: int
    gmail_id: str
    summary: Optional[str]
    priority: Optional[str]
    category: Optional[str]
    triaged_at: Optional[datetime]
    
    class Config:
        from_attributes = True

class Settings(BaseModel):
    key: str
    value: str

class TriageResult(BaseModel):
    summary: str
    priority: str
    category: str

class WebSocketMessage(BaseModel):
    type: str
    email_id: Optional[int]
    html: Optional[str]