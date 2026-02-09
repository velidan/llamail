from email_service.config import settings
from email_service.services.token_budget import count_tokens, truncate_to_budget


def chunk_text(text: str) -> list[dict]:
    chunk_size = settings.chunk_size
    overlap = settings.chunk_overlap

    chunks = []
    start = 0

    while start < len(text):
        end = start + _tokens_to_chars(text[start:], chunk_size)

        chunk_content = text[start:end]

        chunks.append(
            {
                "chunk_index": len(chunks),
                "content": chunk_content,
                "token_count": count_tokens(chunk_content),
                "start_char": start,
                "end_char": end,
            }
        )

        advance = _tokens_to_chars(text[start:], chunk_size - overlap)
        start += max(advance, 1)

    return chunks


def _tokens_to_chars(text: str, max_tokens: int) -> int:
    truncated = truncate_to_budget(text, max_tokens)
    return len(truncated)
