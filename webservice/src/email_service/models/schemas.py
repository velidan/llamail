from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field


class ProcessEmailRequest(BaseModel):
    account_id: str
    gmail_id: str
    thread_id: str | None = None
    rfc822_message_id: str | None = None

    from_address: str
    from_name: str | None = None
    to_addresses: list[str] = []
    cc_addresses: list[str] = []

    subject: str | None = None
    body_text: str
    snippet: str | None = None
    received_at: datetime

    gmail_labels: list[str] = []
    attachments: list[dict] = []


class ProcessEmailResponse(BaseModel):
    status: str = "ok"
    email_id: str
    gmail_link: str | None = None
    summary: str | None = None
    category: str | None = None
    priority: str | None = None
    action_required: bool = False
    tokens_used: int = 0
    processing_time_ms: int = 0


class HealthResponse(BaseModel):
    status: str = "ok"
    llm: bool = False
    database: bool = False
    chromadb: bool = False
    version: str = "2.0.0"


class AskRequest(BaseModel):
    question: str
    account_filter: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    max_results: int = Field(default=5, ge=1, le=20)
    include_sources: bool = True


class EmailSource(BaseModel):
    email_id: str
    from_address: str
    subject: str | None = None
    date: datetime
    relevance_score: float = 0.0


class AskResponse(BaseModel):
    answer: str
    sources: list[EmailSource] = []
    tokens_used: int = 0
    processing_time_ms: int = 0


class DraftRequest(BaseModel):
    email_id: str
    instructions: str
    tone: str = "formal"
    include_thread: bool = True
    max_thread_emails: int = Field(default=3, ge=1, le=10)


class DraftResponse(BaseModel):
    draft: str
    subject: str
    to: list[str]
    account_id: str
    suggestions: list[str] = []
    tokens_used: int = 0


class TelegramCommandRequest(BaseModel):
    text: str
    chat_id: str | int


class TelegramCommandResponse(BaseModel):
    reply: str
