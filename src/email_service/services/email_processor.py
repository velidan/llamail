import json
import logging
import time
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader

from email_service.config import settings
from email_service.models.database import Email, EmailChunk, get_session
from email_service.models.schemas import ProcessEmailRequest, ProcessEmailResponse
from email_service.services import chunker, llm, token_budget
from email_service.services import embeddings

logger = logging.getLogger(__name__)

# --- Load Jinja2 templates ---
_template_dir = Path(__file__).parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_template_dir)))

_summarize = _env.get_template("summarize.j2")
_summarize_chunk = _env.get_template("summarize_chunk.j2")
_summarize_master = _env.get_template("summarize_master.j2")


# --- public entry point ---
def process_email(request: ProcessEmailRequest) -> ProcessEmailResponse:
    start = time.time()
    email_id = f"{request.account_id}_{request.gmail_id}"

    if token_budget.needs_chunking(request.body_text):
        result = _process_chunked(request, email_id)
    else:
        result = _process_single(request, email_id)

    result.processing_time_ms = int((time.time() - start) * 1000)
    return result


def _build_gmail_link(rfc822_message_id: str | None) -> str | None:
    if not rfc822_message_id:
        return None
    clean_id = rfc822_message_id.strip("<>")
    return f"https://mail.google.com/mail/u/0/#search/rfc822msgid%3A{quote(clean_id)}"


# --- single email path (short mail) ---
def _process_single(
    request: ProcessEmailRequest, email_id: str
) -> ProcessEmailResponse:
    truncated_body = token_budget.truncate_to_budget(
        request.body_text, settings.summary_content_budget
    )
    attachment_names = (
        [a["filename"] for a in request.attachments] if request.attachments else []
    )

    # FIX: Defined truncated_body first to avoid NameError
    safe_body = (
        truncated_body
        if (truncated_body and truncated_body.strip())
        else "[No content available]"
    )

    prompt = _summarize.render(
        from_name=request.from_name,
        from_address=request.from_address,
        subject=request.subject,
        date=request.received_at,
        body=safe_body,
        attachments=attachment_names,
    )

    if not prompt.strip():
        logger.error(f"Rendered empty prompt for email {email_id}")
        parsed = {"summary": "Error: Empty prompt rendered", "category": "error"}
    else:
        raw = llm.generate(prompt)
        parsed = _parse_json(raw)

    tokens_used = token_budget.count_tokens(prompt)

    embed_text = (
        f"From: {request.from_name or 'Unknown'}\n"
        f"Subject: {request.subject}\n"
        f"Summary: {parsed.get('summary','')}"
    )
    if request.attachments:
        filenames = ", ".join(a["filename"] for a in request.attachments)
        embed_text += f"\nAttachments: {filenames}"
    embeddings.store(
        doc_id=email_id,
        text=embed_text,
        metadata={
            "account_id": request.account_id,
            "from_address": request.from_address,
            "subject": request.subject or "",
            "type": "email",
        },
    )

    _save_email(request, email_id, parsed)

    return ProcessEmailResponse(
        email_id=email_id,
        gmail_link=_build_gmail_link(request.rfc822_message_id),
        summary=parsed.get("summary"),
        category=parsed.get("category"),
        priority=parsed.get("priority"),
        action_required=parsed.get("action_required", False),
        tokens_used=tokens_used,
    )


# --- chunked email handle (long emails) ---
def _process_chunked(
    request: ProcessEmailRequest, email_id: str
) -> ProcessEmailResponse:
    chunks = chunker.chunk_text(request.body_text)
    logger.info(f"{email_id}: split into {len(chunks)} chunks")

    _save_email(request, email_id, {}, is_chunked=True, chunk_count=len(chunks))

    chunk_summaries = []
    tokens_used = 0

    for chunk in chunks:
        safe_content = chunk["content"] if chunk["content"] else "[Empty Chunk]"

        prompt = _summarize_chunk.render(
            chunk_index=chunk["chunk_index"] + 1,
            total_chunks=len(chunks),
            from_name=request.from_name,
            subject=request.subject,
            content=safe_content,
        )

        if not prompt.strip():
            continue

        raw = llm.generate(prompt)
        parsed_chunk = _parse_json(raw)
        summary = parsed_chunk.get("chunk_summary", raw)
        chunk_summaries.append(summary)
        tokens_used += token_budget.count_tokens(prompt)

        _save_chunk(email_id, chunk, summary)

    master_prompt = _summarize_master.render(
        from_name=request.from_name,
        from_address=request.from_address,
        subject=request.subject,
        date=request.received_at,
        chunk_summaries=chunk_summaries,
    )

    raw_master = llm.generate(master_prompt)
    parsed_master = _parse_json(raw_master)

    tokens_used += token_budget.count_tokens(master_prompt)

    _save_email(
        request, email_id, parsed_master, is_chunked=True, chunk_count=len(chunks)
    )

    return ProcessEmailResponse(
        email_id=email_id,
        gmail_link=_build_gmail_link(request.rfc822_message_id),
        summary=parsed_master.get("summary"),
        category=parsed_master.get("category"),
        priority=parsed_master.get("priority"),
        action_required=parsed_master.get("action_required", False),
        tokens_used=tokens_used,
    )


# --- parse json from llm response ---
def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    logger.warning(f"Failed to parse LLM JSON: {text[:200]}")
    return {"summary": text}


# --- save to sqlite ---
def _save_email(request, email_id, parsed, is_chunked=False, chunk_count=0):
    session = get_session()
    try:
        email = Email(
            id=email_id,
            account_id=request.account_id,
            gmail_id=request.gmail_id,
            thread_id=request.thread_id,
            from_address=request.from_address,
            from_name=request.from_name,
            to_addresses=json.dumps(request.to_addresses),
            cc_addresses=json.dumps(request.cc_addresses),
            subject=request.subject,
            body_text=request.body_text,
            snippet=request.snippet,
            received_at=request.received_at,
            summary=parsed.get("summary"),
            category=parsed.get("category"),
            priority=parsed.get("priority"),
            sentiment=parsed.get("sentiment"),
            action_required=parsed.get("action_required", False),
            action_items=json.dumps(parsed.get("action_items", [])),
            key_people=json.dumps(parsed.get("key_people", [])),
            attachments=(
                json.dumps(request.attachments) if request.attachments else None
            ),
            has_embedding=True,
            is_chunked=is_chunked,
            chunk_count=chunk_count,
        )
        session.merge(email)
        session.commit()
    finally:
        session.close()


def _save_chunk(email_id, chunk, summary):
    session = get_session()
    try:
        db_chunk = EmailChunk(
            email_id=email_id,
            chunk_index=chunk["chunk_index"],
            content=chunk["content"],
            token_count=chunk["token_count"],
            start_char=chunk["start_char"],
            end_char=chunk["end_char"],
            summary=summary,
            has_embedding=True,
        )
        session.add(db_chunk)
        session.commit()
    finally:
        session.close()
