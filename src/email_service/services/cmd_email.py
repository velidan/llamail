"""
Email commands for tg handler:
search, recent, show, ask, delete, block, unsubscribe, gramar, chitchat
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from email_service.models.database import Email, EmailChunk, get_session
from email_service.services import llm, embeddings, gmail_client, chat_memory
from email_service.services.handler_state import (
    clear_results,
    set_result,
    resolve_email_ref,
)
from email_service.services.search import hybrid_search
from email_service.services.utils import parse_json

logger = logging.getLogger(__name__)

__template_dir = Path(__file__).parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(__template_dir)))
_ask_template = _env.get_template("ask.j2")
_chitchat_template = _env.get_template("shitchat.j2")
_grammar_template = _env.get_template("grammar.j2")


def search(args: list[str]) -> str:
    if not args:
        return "Please provide a search query.\nExample: /search budget Q2"

    after_date = None
    if len(args) >= 2:
        try:
            after_date = datetime.fromisoformat(args[-1])
            args = args[:-1]
        except ValueError:
            pass

    query = " ".join(args)
    results = hybrid_search(query, max_results=5, after_date=after_date)

    if not results:
        f"No results found for: {query}"

    clear_results()
    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        set_result(i, r["email_id"])
        date = r["received_at"].strftime("%Y-%m-%d") if r["received_at"] else "?"
        sender = r["from_name"] or r["from_address"]
        subject = r["subject"] or "(no subject)"
        summary = r["summary"] or ""
        if len(summary) > 120:
            summary = summary[:120] + "..."
        score_pct = int(r["score"] * 100)

        lines.append(
            f"[(i)]. [{score_pct}%] {subject}\n"
            f"    From: {sender} | {date}\n"
            f"    {summary}"
        )

    return "\n\n".join(lines)


def recent(args: list[str]) -> str:
    count = 5
    if args:
        try:
            count = int(args[0])
            count = min(count, 20)
        except ValueError:
            return f"Invalid count: {args[0]}. Must be a number"

    session = get_session()
    try:
        emails = (
            session.query(Email).order_by(Email.received_at.desc()).limit(count).all()
        )

        if not emails:
            return "No emails found."

        clear_results()
        lines = [f"Last {len(emails)} emails:\n"]
        for i, e in enumerate(emails, 1):
            set_result(i, e.id)
            date = e.received_at.strftime("%Y-%m-%d %H:5M") if e.received_at else "?"
            sender = e.from_name or e.from_address
            subject = e.subject or "(no subject)"
            category = e.category or "?"
            priority = e.priority or "?"

            lines.append(
                f"[{i}] {subject}\n"
                f"    From: {sender} | {date}\n"
                f"    [{category}] [{priority}]"
            )

        return "\n\n".join(lines)
    finally:
        session.close()


def show_email(args: list[str]) -> str:
    if not args:
        return "Usage: show (number)\nExample: show 1 (after search or recent)"

    email_id = resolve_email_ref(args[0])

    session = get_session()
    try:
        email = session.query(Email).filter(Email.id == email_id).first()
        if not email:
            return f"Email not found: {email_id}"

        sender = email.from_name or email.from_address
        date = (
            email.received_at.strftime("%Y-%m-%d %H:%M") if email.received_at else "?"
        )
        subject = email.subject or "(no subject)"
        body = email.body_text or "(no content)"

        header = (
            f"From: {sender} <{email.from_address}>\n"
            f"Date: {date}\n"
            f"Subject: {subject}\n"
            f"---\n"
        )

        attachments_line = ""
        if email.attachments:
            att_list = json.loads(email.attachments)
            if att_list:
                names = ", ".join(a["filename"] for a in att_list)
                attachments_line = f"Attachments: {names}\n"

        max_body = 4096 - len(header) - len(attachments_line) - 20
        if len(body) > max_body:
            body = body[:max_body] + "\n...{truncated}"

        return header + attachments_line + body
    finally:
        session.close()


def delete_email(args: list[str]) -> str:
    if not args:
        return "Usage: delete (number)\mExample: delete 3 (after search or recent)"

    email_id = resolve_email_ref(args[0])

    session = get_session()
    try:
        email = session.query(Email).filter(Email.id == email_id).first()
        if not email:
            return f"Email not found: {email_id}"

        gmail_id = email.gmail_id
        subject = email.subject or "(no subject)"

        try:
            service = gmail_client.get_gmail_service()
            gmail_client.trash_email(service, gmail_id)
        except Exception as e:
            logger.error(f"Gmail trash failed: {e}")
            return f"Failed to trash in Gmail: {e}"

        try:
            embeddings.delete(email_id)
        except Exception as e:
            logger.warning(f"ChromaDB delete failed: {e}")

        session.query(EmailChunk).filter(EmailChunk.email_id == email_id).delete()
        session.query(Email).filter(Email.id == email_id).delete()

        return f"Deleted: {subject}"
    finally:
        session.close()


def block_sender(args: list[str]) -> str:
    if not args:
        return "Usage: block (number)\nExample: block 3 (after search or recent)"

    email_id = resolve_email_ref(args[0])

    session = get_session()
    try:
        email = session.query(Email).filter(Email.id == email_id).first()
        if not email:
            return f"Email not found: {email_id}"

        from_address = email.from_address
        from_name = email.from_name or from_address
    finally:
        session.close()

    try:
        service = gmail_client.get_gmail_service()
        gmail_client.block_sender(service, from_address)
    except Exception as e:
        logger.error(f"Block sender failed: {e}")
        return f"Failed to block {from_address}: {e}"

    return f"Blocked: {from_name} <{from_address}>\nFuture emails from this sender will be auto-trashed"


def unsubscribe(args: list[str]) -> str:
    if not args:
        return "Usage: unsubscribe (number)\nExample: unsubscribe 3 (after search or recent)"

    email_id = resolve_email_ref(args[0])

    session = get_session()
    try:
        email = session.query(Email).filter(Email.id == email_id).first()
        if not email:
            return f"Email not found: {email_id}"

        gmail_id = email.gmail_id
        from_address = email.from_address
        from_name = email.from_name or from_address
    finally:
        session.close()

    try:
        service = gmail_client.get_gmail_service()
        info = gmail_client.get_unsubscribe_info(service, gmail_id)
    except Exception as e:
        logger.error("Unsubscribe lookup failed: {e}")
        return f"Failed to check unsubscribe info: {e}"

    if not info["found"]:
        return (
            f"No unsubscribe header found for {from_name}.\n"
            f"You can block this sender instead: block {args[0]}"
        )

    if info["mailto"]:
        try:
            mailto = info["mailto"]
            unsub_address = mailto.split("?")[0]
            subject = "Unsubscribe"
            if "?" in mailto:
                params = mailto.split("?", 1)[1]
                for param in params.split("&"):
                    if param.lower().startswith("subject="):
                        subject = param.split("=", 1)[1]

            gmail_client.send_email(
                service, to=unsub_address, subject=subject, body="Unsubscribe"
            )
            return f"Unsubscribe email sent to {unsub_address} for {from_name}."
        except Exception as e:
            logger.error(f"Unsubscribe mailto failed: {e}")
            return f"Failed to send unsubscribe email: {e}"

    if info["url"]:
        return (
            f"Unsubscribe from {from_name}:\n"
            f"{info['url']}\n\n"
            f"Click the link above to unsubscribe."
        )

    return (
        f"Unsubscribe header found but cloudn't parse it for {from_name}.\n"
        f"You can block this sender instead: block {args[0]}"
    )


def gramar(args: list[str]) -> str:
    if not args:
        return "Usage: gramar (text)\nExample: gramar I wants to meeting on tuesday"

    text = " ".join(args)
    prompt = _grammar_template.render(text=text)
    return llm.generate(prompt, json_mode=False)


def ask(args: list[str], chat_id: str = "") -> str:
    if not args:
        return "Please provide a question.\nExample: ask Why did Jogn say about the budget?"

    question = " ".join(args)
    results = hybrid_search(question, max_results=5)

    if not results:
        return f"No relevant emails found for: {question}"

    history = chat_memory.get_recent(chat_id)
    history_text = chat_memory.format_for_prompt(history)

    prompt = _ask_template.render(
        emails=results, question=question, conversation_history=history_text
    )
    raw = llm.generate(prompt)
    parsed = parse_json(raw)

    answer = parsed.get("answer", raw)
    confidence = parsed.get("confidence", "unknown")

    lines = [f"{answer}\n", f"Confidence: {confidence}"]
    good_sources = [(i, r) for i, r in enumerate(results, 1) if r["score"] > 0.3]
    if good_sources:
        lines.append(f"Sources ({len(good_sources)}):")
        for i, r in good_sources:
            sender = r["from_name"] or r["from_address"]
            subject = r["subject"] or "(no subject)"
            lines.append(f"    [{i}] {subject} - {sender}")

    return "\n".join(lines)


def chitchat(text: str, chat_id: str) -> str:
    try:
        history = chat_memory.get_recent(chat_id)
        history_text = chat_memory.format_for_prompt(history)

        prompt = _chitchat_template.render(
            message=text, conversation_history=history_text
        )

        return llm.generate(prompt, json_mode=False)
    except Exception as e:
        logger.error(f"Chitchat failed: {e}")
        return "...System nominal. How may I assist you, Operator"
