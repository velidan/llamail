import logging
from datetime import datetime

from sqlalchemy import desc

from email_service.config import settings
from email_service.models.database import ChatMessage, get_session
from email_service.services.token_budget import count_tokens

logger = logging.getLogger(__name__)


def save_message(chat_id: str, role: str, content: str) -> None:
    session = get_session()
    try:
        msg = ChatMessage(
            chat_id=str(chat_id), role=role, content=content, created_at=datetime.now()
        )
        session.add(msg)
        session.commit()
    finally:
        session.close()


def get_recent(chat_id: str) -> list[dict]:
    session = get_session()
    try:
        rows = (
            session.query(ChatMessage)
            .filter(ChatMessage.chat_id == str(chat_id))
            .order_by(desc(ChatMessage.created_at))
            .limit(settings.chat_history_limit * 2)
            .all()
        )

        messages = [
            {"role": r.role, "content": r.content}
            # asc order
            for r in reversed(rows)
        ]
        return messages
    finally:
        session.close()


def format_for_prompt(messages: list[dict]) -> str:
    if not messages:
        return ""

    budget = settings.chat_history_token_budget
    lines = []
    total_tokens = 0

    for msg in reversed(messages):
        role = "You" if msg["role"] == "user" else "Assistant"
        line = f"{role}: {msg['content']}"
        line_tokens = count_tokens(line)

        if total_tokens + line_tokens > budget:
            break
        lines.append(line)
        total_tokens += line_tokens

    lines.reverse()
    return "\n".join(lines)
