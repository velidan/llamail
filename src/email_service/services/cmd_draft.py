"""
Draft commands for tg handler:
draft reply, draft new, send, schedule list, schedule cancel
"""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from email_service.models.database import Email, Draft, get_session
from email_service.services import llm, gmail_client
from email_service.services.handler_state import (
    resolve_email_ref,
)
from email_service.services.utils import parse_json
from email_service.config import settings

logger = logging.getLogger(__name__)

__template_dir = Path(__file__).parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(__template_dir)))
_draft_reply_template = _env.get_template("draft_reply.j2")
_draft_new_template = _env.get_template("draft_new.j2")


def handle_draft(args: list[str]) -> str:
    if not args:
        return (
            "Usage: \n"
            "draft reply (email_id) (instructions)\n"
            "draft new (recipients) (instructions)"
        )

    action = args[0].lower()

    if action == "reply":
        return draft_reply(args[1:])

    if action == "new":
        return draft_new(args[1:])

    return f"Unknown draft action: {action}\n" "Usage: draft reply or draft new"


def draft_reply(args: list[str]) -> str:
    import email_service.services.handler_state as state

    if len(args) < 2:
        return (
            "Usage: draft reply (email_id) (instructions)\n"
            "Example: draft reply 1 agree but suggest Thursday"
        )

    email_id = resolve_email_ref(args[0])
    instructions = " ".join(args[1:])

    session = get_session()
    try:
        email = session.query(Email).filter(Email.id == email_id).first()
        if not email:
            return f"Email not found: {email_id}"

        from_name = email.from_name
        from_address = email.from_address
        to_addresses = email.to_addresses or ""
        subject = email.subject
        received_at = email.received_at
        body_text = email.body_text or email.snippet or "(no content)"
        summary = email.summary
        thread_id = email.thread_id
    finally:
        session.close()

    account_id = email_id.split("_", 1)[0]

    prompt = _draft_reply_template.render(
        from_name=from_name,
        from_address=from_address,
        to_addresses=to_addresses,
        subject=subject,
        received_at=received_at,
        body_text=body_text,
        summary=summary,
        instructions=instructions,
    )

    raw = llm.generate(prompt)
    parsed = parse_json(raw)

    reply_body = parsed.get("reply_body", raw)
    suggested_subject = parsed.get("suggested_subject", f"Re: {subject or ''}")

    session = get_session()
    try:
        draft = Draft(
            account_id=account_id,
            draft_type="reply",
            to_address=from_address,
            subject=suggested_subject,
            body=reply_body,
            original_email_id=email_id,
            thread_id=thread_id,
        )
        session.add(draft)
        session.commit()
        state._last_draft_id = draft.id
    finally:
        session.close()

    return (
        f"Draft reply to: {from_name or from_address} (#{state._last_draft_id})\n"
        f"Subject: {suggested_subject}\n"
        f"---\n"
        f"{reply_body}\n"
        f"---\n"
        f"Type 'send' to send this draft"
    )


def draft_new(args: list[str]) -> str:
    import email_service.services.handler_state as state

    if len(args) < 2:
        return (
            "Usage: draft new (recipient) (instructions)\n"
            "Example: draft new jogn@example.com ask about project deadline"
        )

    to_address = args[0]
    instructions = " ".join(args[1:])

    recipient_name = None
    session = get_session()
    try:
        known = session.query(Email).filter(Email.from_address == to_address).first()
        if known:
            recipient_name = known.from_name
    finally:
        session.close()

    prompt = _draft_new_template.render(
        to_address=to_address, recipient_name=recipient_name, instructions=instructions
    )

    raw = llm.generate(prompt)
    parsed = parse_json(raw)

    email_body = parsed.get("email_body", raw)
    suggested_subject = parsed.get("suggested_subject", "")

    session = get_session()
    try:
        draft = Draft(
            account_id=settings.default_account,
            draft_type="new",
            to_address=to_address,
            subject=suggested_subject,
            body=email_body,
        )
        session.add(draft)
        session.commit()
        state._last_draft_id = draft.id
    finally:
        session.close()

    return (
        f"Draft to: {to_address} (#{state._last_draft_id})\n"
        f"Subject: {suggested_subject}\n"
        f"---\n"
        f"{email_body}\n"
        f"---\n"
        f"Type 'send' to send this draft"
    )


def send_email(args: list[str]) -> str:
    import email_service.services.handler_state as state

    draft_id = None
    time_args = []

    for i, arg in enumerate(args):
        if arg.lower() in ("at", "in"):
            time_args = args[i:]
            break
        elif arg.isdigit() and draft_id is None:
            draft_id = int(arg)

    if draft_id is None:
        if state._last_draft_id:
            draft_id = state._last_draft_id
        else:
            return (
                "No draft to send. Create one with 'draft reply' or 'draft new' first."
            )

    session = get_session()
    try:
        draft = session.query(Draft).filter(Draft.id == draft_id).first()
        if not draft:
            return f"Draft: #{draft_id} not found."

        if draft.status == "sent":
            return f"Draft #{draft_id} was already sent."

        if draft.status == "scheduled":
            return (
                f"Draft #{draft_id} is already scheduled for "
                f"{draft.scheduled_at.strftime('%Y-%m-%d %H:%M')}.\n"
                f"Use 'schedule cancel {draft_id}' to cancel first."
            )

        if time_args:
            send_at = _parse_time(time_args)
            if isinstance(send_at, str):
                return send_at

            draft.status = "scheduled"
            draft.scheduled_at = send_at
            session.commit()

            return (
                f"Scheduled!\n"
                f"Draft #{draft_id} to {draft.to_address}\n"
                f"Subject: {draft.subject}\n"
                f"Will send at: {send_at.strftime('%Y-%m-%d %H:%M')}"
            )

        service = gmail_client.get_gmail_service()
        gmail_client.send_email(
            service,
            to=draft.to_address,
            subject=draft.subject or "",
            body=draft.body,
            thread_id=draft.thread_id,
        )

        draft.status = "sent"
        session.commit()

        return f"Email sent!\nTo: {draft.to_address}\nSubject: {draft.subject}"
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return f"Failed to send draft #{draft_id}: {e}"
    finally:
        session.close()


# TODO: Maybe move it's to some utility
def _parse_time(args: list[str]) -> datetime | str:
    """Parse 'at HH:MM' or 'in 2h30m'. Returns datetime or error str."""
    keyword = args[0].lower()
    if len(args) < 2:
        return "Missing time value. Examples: 'at 14:30' or 'in 2h'"

    value = " ".join(args[1:])

    if keyword == "at":
        try:
            time_part = datetime.strptime(value.strip(), "%H:%M")
            now = datetime.now()
            send_at = now.replace(
                hour=time_part.hour, minute=time_part.minute, second=0, microsecond=0
            )
            if send_at <= now:
                send_at += timedelta(days=1)
            return send_at
        except ValueError:
            return f"Invalid time format: '{value}'. Use HH:MM (eg. 14:30)"

    if keyword == "in":
        match = re.match(r"(?:(\d+)h)?(?:(\d+)m)?$", value.strip())
        if not match or not any(match.groups()):
            return f"Invalid delay format: '{value}'. Use e.g. '2h', '30m', '1h30m'"

        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)

        if hours == 0 and minutes == 0:
            return "Delay must be at least 1 minute"

        return datetime.now() + timedelta(hours=hours, minutes=minutes)
    return f"Unknown time keyword: '{keyword}'. Use 'at' or 'in'."


def handle_schedule(args: list[str]) -> str:
    if not args:
        return "Usage: schedule list | schedule cancel (draft_id)"

    action = args[0].lower()

    if action == "list":
        return schedule_list()

    if action == "cancel":
        return schedule_cancel(args[1:])

    return f"Unknown schedule action: {action}\nUsage: schedule list | schedule cancel (draft_id)"


def schedule_list() -> str:
    session = get_session()
    try:
        drafts = (
            session.query(Draft)
            .filter(Draft.status == "scheduled")
            .order_by(Draft.scheduled_at)
            .all()
        )

        if not drafts:
            return "No scheduled sends."

        lines = ["Scheduled sends:\n"]
        for d in drafts:
            time_str = d.scheduled_at.strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"[#{d.id}] {d.to_address}\n"
                f"    Subject: {d.subject}\n"
                f"    Send at: {time_str}"
            )

        return "\n\n".join(lines)
    finally:
        session.close()


def schedule_cancel(args: list[str]) -> str:
    if not args or not args[0].isdigit():
        return "Usage: schedule cancel (draft_id)\nExample: schedule cancel 5"

    draft_id = int(args[0])

    session = get_session()
    try:
        draft = session.query(Draft).filter(Draft.id == draft_id).first()
        if not draft:
            return f"Draft #{draft_id} not found"

        if draft.status != "scheduled":
            return f"Draft #{draft_id} is not scheduled (status: {draft.status})."

        draft.status = "draft"
        draft.scheduled_at = None
        session.commit()

        return (
            f"Cancelled scheduled send for draft #{draft_id}.\n"
            f"Draft restored. Use 'send {draft_id}' to send immediately"
        )
    finally:
        session.close()
