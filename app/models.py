from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.database import Base

class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    display_name = Column(String)
    color = Column(String)  # Hex color for UI differentiation
    access_token = Column(Text)
    refresh_token = Column(Text)
    token_expiry = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationship to emails
    emails = relationship("Email", back_populates="account")

class Email(Base):
    __tablename__ = "emails"
    
    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"))
    gmail_id = Column(String, index=True)
    sender = Column(String)
    subject = Column(String)
    snippet = Column(Text)
    body = Column(Text)
    received_at = Column(DateTime)
    is_read = Column(Boolean, default=False)
    is_archived = Column(Boolean, default=False)
    
    # Triage fields
    summary = Column(Text, nullable=True)
    relevance_score = Column(Integer, nullable=True)  # 0-100
    category = Column(String, nullable=True)
    action_required = Column(Boolean, default=False)
    scan_model = Column(String, nullable=True)
    triaged_at = Column(DateTime, nullable=True)
    
    # Relationships
    account = relationship("Account", back_populates="emails")
    attachments = relationship("Attachment", back_populates="email", cascade="all, delete-orphan")

class Attachment(Base):
    __tablename__ = "attachments"
    
    id = Column(Integer, primary_key=True, index=True)
    email_id = Column(Integer, ForeignKey("emails.id", ondelete="CASCADE"), index=True)
    filename = Column(String, nullable=False)
    mime_type = Column(String, default="application/octet-stream")
    size = Column(Integer, default=0)
    gmail_attachment_id = Column(String, nullable=False)
    
    email = relationship("Email", back_populates="attachments")

class Settings(Base):
    __tablename__ = "settings"
    
    key = Column(String, primary_key=True)
    value = Column(String)

