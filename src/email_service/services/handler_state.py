"""
Shared state for Telegram command handlers.

Avoids circular imports betwee cmd_*.py and telegram_handler.py
"""

import re

# Number aliases from last search/recent: {1: "full_email_id", 2: "full_email_id", ... }
_last_results: dict[int, str] = {}

# last created draft ID for quick "send" without specifying ID
_last_draft_id: int | None = None


def resolve_email_ref(ref: str) -> str:
    """Resolve a number ailas or raw email ID from the user input."""
    cleaned = re.sub(r"\D", "", ref)
    if cleaned and int(cleaned) in _last_results:
        return _last_results[int(cleaned)]
    return ref
