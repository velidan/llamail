"""
Shared state for Telegram command handlers.

Avoids circular imports betwee cmd_*.py and telegram_handler.py
"""

import re

# Number aliases from last search/recent: {1: "full_email_id", 2: "full_email_id", ... }
_last_results: dict[int, str] = {}

# last created draft ID for quick "send" without specifying ID
_last_draft_id: int | None = None


class RefNotFoundError(Exception):
    pass


def resolve_email_ref(ref: str) -> str:
    """Resolve a number ailas or raw email ID from the user input."""
    cleaned = re.sub(r"\D", "", ref)
    if cleaned:
        num = int(cleaned)
        if num in _last_results:
            return _last_results[num]
        if not _last_results:
            raise RefNotFoundError(
                "No results to reference. Run /recent or /search first, then use the number."
            )
        raise RefNotFoundError(
            f"Number [{num}] not in results. Available: 1-{max(_last_results.keys())}"
        )
    return ref


def clear_results():
    _last_results.clear()


def set_result(index: int, email_id: str):
    _last_results[index] = email_id
