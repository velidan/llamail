import tiktoken

from email_service.config import settings

_encoding = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding.encode(text))


def truncate_to_budget(text: str, max_tokens: int) -> str:
    tokens = _encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return _encoding.decode(tokens[:max_tokens])


def needs_chunking(text: str) -> bool:
    return count_tokens(text) > settings.chunk_threshold
