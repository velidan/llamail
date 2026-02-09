import json
import logging
import time
from pathlib import Path

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

# --- publ entry point


def process_email(request: ProcessEmailRequest) -> ProcessEmailResponse:
    start = time.time()

    email_id = f"{request.account_id}_{request.gmail_id}"

    if token_budget.needs_chunking(request.body_text):
        result = _process_chunked(request, email_id)
    else:
        result = _process_single(request, email_id)

    result.processing_time_ms = int((time.time() - start) * 1000)
    return result


# --- single email path (short mail)


def _process_single(
    request: ProcessEmailRequest, email_id: str
) -> ProcessEmailResponse:
    # trunc body to token buget
    body = token_budget.truncate_to_budget(
        request.body_text, settings.summary_content_budget
    )

    # render prompt and call llm
    prompt = _summarize.render(
        from_name=request.from_name,
        from_address=request.from_address,
        subject=request.subject,
        date=request.received_at,
        body=body,
    )
    raw = llm.generate(prompt)
    parsed = _parse_json(raw)
    tokens_used = token_budget.count_tokens(prompt)

    # buld embedding txt and store it in the ChromDB
    embed_text = (
        f"From: {request.from_name or 'Unknown'}\n"
        f"Subject: {request.subject}\n"
        f"Summary: {parsed.get('summary','')}"
    )
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
    # save into sqlite
    _save_email(request, email_id, parsed)

    return ProcessEmailResponse(
        email_id=email_id,
        summary=parsed.get("summary"),
        category=parsed.get("category"),
        priority=parsed.get("priority"),
        action_required=parsed.get("action_required", False),
        tokens_used=tokens_used,
    )


# chunked email hanle (long emails)


def _process_chunked(
    request: ProcessEmailRequest, email_id: str
) -> ProcessEmailResponse:
    # split into chunks
    chunks = chunker.chunk_text(request.body_text)
    logger.info(f"{email_id}: split into {len(chunks)} chunks")

    # summarize every chuk
    chunk_summaries = []
    tokens_used = 0

    for chunk in chunks:
        prompt = _summarize_chunk.render(
            chunk_index=chunk["chunk_index"] + 1,
            total_chunks=len(chunks),
            from_name=request.from_name,
            subject=request.subject,
            content=chunk["content"],
        )

        raw = llm.generate(prompt)
        parsed_chunk = _parse_json(raw)
        summary = parsed_chunk.get("chunk_summary", raw)
        chunk_summaries.append(summary)
        tokens_used += token_budget.count_tokens(prompt)

        # store chunk embed into chromaDB
        embed_text = (
            f"From: {request.from_name or 'Unknown'}\n"
            f"Subject: {request.subject}\n"
            f"{summary}"
        )
        embeddings.store(
            doc_id=f"{email_id}_chunk_{chunk['chunk_index']}",
            text=embed_text,
            metadata={
                "account_id": request.account_id,
                "subject": request.subject or "",
                "type": "chunk",
                "parent_email_id": email_id,
                "chunk_index": chunk["chunk_index"],
            },
        )

        # save chunk in sqlit
        _save_chunk(email_id, chunk, summary)

    # master summary from all the chhunk summaries
    master_prompt = _summarize_master.render(
        from_name=request.from_name,
        from_address=request.from_address,
        subject=request.subject,
        date=request.received_at,
        chunk_summaries=chunk_summaries,
    )
    raw = llm.generate(master_prompt)
    parsed = _parse_json(raw)
    tokens_used += token_budget.count_tokens(master_prompt)

    # put master embed into chromadb
    embed_text = (
        f"From: {request.from_name or 'Unknown'}\n"
        f"Subject: {request.subject}\n"
        f"Summary: {parsed.get('summary', '')}"
    )
    chunks_ln = len(chunks)
    embeddings.store(
        doc_id=f"{email_id}_master",
        text=embed_text,
        metadata={
            "account_id": request.account_id,
            "from_address": request.from_address,
            "subject": request.subject or "",
            "type": "master",
            "is_chunked": True,
            "chunk_count": chunks_ln,
        },
    )

    _save_email(request, email_id, parsed, is_chunked=True, chunk_count=chunks_ln)

    return ProcessEmailResponse(
        email_id=email_id,
        summary=parsed.get("summary"),
        category=parsed.get("category"),
        priority=parsed.get("priority"),
        action_required=parsed.get("action_required", False),
        tokens_used=tokens_used,
    )


# --- parse json from llm response ---


def _parse_json(raw: str) -> dict:
    text = raw.strip()

    # strip markdown code if lllm wraps json in ```
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # try to find JSON object within the text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    logger.warning(f"Failed to parse LLM JSON: {text[:200]}")
    return {"summary": text}


# --- save email to sqplite ---


def _save_email(
    request: ProcessEmailRequest,
    email_id: str,
    parsed: dict,
    is_chunked: bool = False,
    chunk_count: int = 0,
):
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
            has_embedding=True,
            is_chunked=is_chunked,
            chunk_count=chunk_count,
        )
        session.merge(email)
        session.commit()
    finally:
        session.close()


# --- save a chunk to sqlite


def _save_chunk(email_id: str, chunk: dict, summary: str):
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
