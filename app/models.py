from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

from app.database import Base

class PriorityTier(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    display_name = Column(String)
    color = Column(String)  # Hex color for UI differentiation
    access_token = Column(Text)
    refresh_token = Column(Text)
    token_expiry = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    
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
    priority = Column(String, nullable=True)  # PriorityTier enum as string
    category = Column(String, nullable=True)
    triaged_at = Column(DateTime, nullable=True)
    
    # Relationship to account
    account = relationship("Account", back_populates="emails")

class Settings(Base):
    __tablename__ = "settings"
    
    key = Column(String, primary_key=True)
    value = Column(String)