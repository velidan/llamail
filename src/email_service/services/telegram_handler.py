import logging
import re
from datetime import datetime, timedelta

from email_service.services import import_coordinator, gmail_client
from email_service.services.search import hybrid_search
from email_service.models.database import Email, Draft, get_session

from email_service.services import llm, embeddings
from email_service.services.email_processor import _parse_json
from email_service.services import chat_memory
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

_template_dir = Path(__file__).parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(_template_dir)))
_ask_template = _env.get_template("ask.j2")
_classify_template = _env.get_template("classify_intent.j2")
_chitchat_template = _env.get_template("chitchat.j2")
_draft_reply_template = _env.get_template("draft_reply.j2")
_draft_new_template = _env.get_template("draft_new.j2")
_grammar_template = _env.get_template("grammar.j2")

HELP_TEXT = """Available commands:

search (query)
ask (question)
recent [count]
show (number)
delete (number)
block (number)
unsubscribe (number)
grammar (text)
draft reply (email_id) (instructions)
draft new (recipient) (instructions)
send [draft_id]
send [draft_id] at HH:MM
send [draft_id] in 2h30m
schedule list
schedule cancel (draft_id)
import start (account) [count|all]
import pause (account)
import resume (account)
import status
import history (account)
accounts
help"""

CHITCHAT_RESPONSES = [
    "I'm here to help with your emails. Type 'help' to see what I can do."
]
# it's a registry for number aliases for emails eg {1: "full_email_id", 2: "full_email_id", ...}. Just don't want to type all the email ids to reply etc.
_last_results: dict[int, str] = {}
_last_draft_id: int | None = None


def handle_command(text: str, chat_id: str | int = "") -> str:
    text = text.strip()
    if not text:
        return "Empty message. Type 'help' for commands."

    chat_id_str = str(chat_id)
    chat_memory.save_message(chat_id_str, "user", text)

    parts = text.split()
    command = parts[0].lower()

    if command == "help":
        reply = HELP_TEXT
    elif command == "accounts":
        reply = _accounts_info()
    elif command == "search":
        reply = _search(parts[1:])
    elif command == "recent":
        reply = _recent(parts[1:])
    elif command == "import":
        reply = _handle_import(parts[1:])
    elif command == "show":
        reply = _show_email(parts[1:])
    elif command == "delete":
        reply = _delete_email(parts[1:])
    elif command == "block":
        reply = _block_sender(parts[1:])
    elif command == "unsubscribe":
        reply = _unsubscribe(parts[1:])
    elif command == "grammar":
        reply = _grammar(parts[1:])
    elif command == "ask":
        reply = _ask(parts[1:], chat_id_str)
    elif command == "draft":
        reply = _handle_draft(parts[1:])
    elif command == "send":
        reply = _send_email(parts[1:])
    elif command == "schedule":
        reply = _handle_schedule(parts[1:])
    else:
        reply = _llm_route(text, chat_id_str)

    chat_memory.save_message(chat_id_str, "assistant", reply)
    return reply


def _handle_import(args: list[str]) -> str:
    if not args:
        return "Please specify an action: start, pause, resume, status, or history"
    action = args[0].lower()

    if action == "status":
        return _import_status()

    if action == "start":
        return _import_start(args[1:])

    if action == "pause":
        return _import_pause(args[1:])

    if action == "resume":
        return _import_resume(args[1:])

    if action == "history":
        return _import_history(args[1:])

    return f"Unknown import action: {action}\nPlease specify an action: start, pause, resume, status, or history"


def _import_status() -> str:

    jobs = import_coordinator.list_all_jobs()
    if not jobs:
        return "No import jobs found."

    latest = {}
    for job in jobs:
        acct = job["account_id"]
        if acct not in latest:
            latest[acct] = job

    lines = ["Import Status:\n"]
    for acct, job in latest.items():
        total = job["total_emails"]
        done = job["processed_count"]
        failed = job["failed_count"]
        skipped = job["skipped_count"]

        if total > 0:
            pct = int((done + skipped) / total * 100)
        else:
            pct = 0

        status_icon = {
            "completed": "done",
            "running": ">>",
            "paused": "||",
            "pending": "..",
        }.get(job["status"], "??")

        lines.append(
            f"[{status_icon}] {acct}\n"
            f"      {done}/{total} processed, {failed} failed, {skipped} skipped ({pct}%)"
        )

    return "\n".join(lines)


def _import_start(args: list[str]) -> str:
    if not args:
        return "Please provide the account email.\nExample: import start user@gmail.com"

    account_id = args[0]
    max_emails = 0
    if len(args) > 1 and args[1].lower() != "all":
        try:
            max_emails = int(args[1])
        except ValueError:
            return f"Invalid count: {args[1]}. Must be a number or 'all'"

    try:
        job = import_coordinator.create_job(account_id, max_emails)

        import threading
        from email_service.services import import_worker

        thread = threading.Thread(
            target=import_worker.run_job, args=(job.id,), daemon=True
        )
        thread.start()

        return (
            f"Import started!\n"
            f"Account: {account_id}\n"
            f"Emails found: {job.total_emails}\n"
            f"New to process: {job.total_emails - job.skipped_count}\n"
            f"Skipped (already imported): {job.skipped_count}"
        )
    except Exception as e:
        logger.error(f"Import start failed: {e}")
        return f"Failed to start import: {e}"


def _import_pause(args: list[str]) -> str:
    if not args:
        return "Please provide the account email.\nExample: import pause user@gmail.com"

    account_id = args[0]
    job_id = import_coordinator.find_active_job(account_id)

    if not job_id:
        return f"No running import found for {account_id}"

    import_coordinator.pause_job(job_id)
    return f"Import paused for {account_id}"


def _import_resume(args: list[str]) -> str:
    if not args:
        return (
            "Please provide the account email.\nExample: import resume user@gmail.com"
        )

    account_id = args[0]
    result = import_coordinator.resume_job(account_id)

    if not result:
        return f"No paused or completed import found for {account_id}"

    import threading
    from email_service.services import import_worker

    thread = threading.Thread(
        target=import_worker.run_job, args=(result["job_id"],), daemon=True
    )
    thread.start()

    if result["reset_count"] > 0:
        return (
            f"Import resumed for {account_id}\n"
            f"Reset {result['reset_count']} failed emails back to pending"
        )

    return f"Import resumed for {account_id}"


def _import_history(args: list[str]) -> str:
    if not args:
        return (
            "Please provide the account email.\nExample: import history user@gmail.com"
        )

    account_id = args[0]
    jobs = import_coordinator.list_all_jobs()
    account_jobs = [j for j in jobs if j["account_id"] == account_id]

    if not account_jobs:
        return f"No import jobs found for {account_id}"

    lines = [f"Import history for {account_id}:\n"]
    for job in account_jobs:
        total = job["total_emails"]
        done = job["processed_count"]
        failed = job["failed_count"]

        status_icon = {
            "completed": "done",
            "running": ">>",
            "paused": "||",
            "pending": "..",
        }.get(job["status"], "??")

        date = (
            job["created_at"].strftime("%Y-%m-%d %H:%M") if job["created_at"] else "?"
        )
        lines.append(
            f"[{status_icon}] Job #{job['job_id']} - {date}\n{done}/{total} processed, {failed} failed"
        )

    return "\n".join(lines)


def _accounts_info() -> str:
    try:
        service = gmail_client.get_gmail_service()
        total = gmail_client.get_total_messages(service)

        jobs = import_coordinator.list_all_jobs()
        imported = 0
        for job in jobs:
            imported += job["processed_count"]

        return (
            f"Connected accounts:\n\n"
            f"sviatoslavbarbutsa@gmail.com\n"
            f"    Total emails on server: {total}\n"
            f"    Imported so far: {imported}"
        )
    except Exception as e:
        return f"Failed to get account info: {e}"


def _search(args: list[str]) -> str:
    if not args:
        return "Please provide a search query.\nExample: search budget Q2"

    query = " ".join(args)
    results = hybrid_search(query, max_results=5)

    if not results:
        return f"No results found for: {query}"

    _last_results.clear()
    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        _last_results[i] = r["email_id"]
        date = r["received_at"].strftime("%Y-%m-%d") if r["received_at"] else "?"
        sender = r["from_name"] or r["from_address"]
        subject = r["subject"] or "(no subject)"
        summary = r["summary"] or ""
        if len(summary) > 120:
            summary = summary[:120] + "..."
        score_pct = int(r["score"] * 100)

        lines.append(
            f"[{i}]. [{score_pct}%] {subject}\n"
            f"   From: {sender} | {date}\n"
            f"   {summary}"
        )

    return "\n\n".join(lines)


def _recent(args: list[str]) -> str:
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

        _last_results.clear()
        lines = [f"Last {len(emails)} emails:\n"]
        for i, e in enumerate(emails, 1):
            _last_results[i] = e.id
            date = e.received_at.strftime("%Y-%m-%d %H:%M") if e.received_at else "?"
            sender = e.from_name or e.from_address
            subject = e.subject or "(no subject)"
            category = e.category or "?"
            priority = e.priority or "?"

            lines.append(
                f"[{i}] {subject}\n"
                f"   From: {sender} | {date}\n"
                f"   [{category}] [{priority}]"
            )

        return "\n\n".join(lines)
    finally:
        session.close()


def _show_email(args: list[str]) -> str:
    if not args:
        return "Usage: show (number)\nExample: show 1 (after search or recent)"

    ref = args[0]
    if ref.isdigit() and int(ref) in _last_results:
        email_id = _last_results[int(ref)]
    else:
        email_id = ref

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

        # telegra msg limit is 4096 chars so must handle that
        header = (
            f"From: {sender} <{email.from_address}>\n"
            f"Date: {date}\n"
            f"Subject: {subject}\n"
            f"---\n"
        )
        max_body = 4096 - len(header) - 20
        if len(body) > max_body:
            body = body[:max_body] + "\n...(truncated)"

        return header + body
    finally:
        session.close()


def _delete_email(args: list[str]) -> str:
    if not args:
        return "Usage: delete (number)\nExample: delete 3 (after search or recent)"

    ref = args[0]
    if ref.isdigit() and int(ref) in _last_results:
        email_id = _last_results[int(ref)]
    else:
        email_id = ref

    session = get_session()
    try:
        email = session.query(Email).filder(Email.id == email_id).first()
        if not email:
            return f"Email not found: {email_id}"

        gmail_id = email.gmail_id
        subject = email.subject or "(no subject)"

        # move to traash in gmail
        try:
            service = gmail_client.get_gmail_service()
            gmail_client.trash_email(service, gmail_id)
        except Exception as e:
            logger.error(f"Gmail trash failed: {e}")
            return f"Failed to trash in Gmail: {e}"

        # get rid of it in chrome
        try:
            embeddings.delete(email_id)
        except Exception as e:
            logger.warning(f"ChromaDb delete failed. Whoops. {e}")

        # delete chunks then email from sqlite
        from email_service.models.database import EmailChunk

        session.query(EmailChunk).filter(EmailChunk.email_id == email_id).delete()
        session.query(Email).filter(Email.id == email_id).delete()
        session.commit()

        return f"Deleted: {subject}"
    finally:
        session.close()


def _block_sender(args: list[str]) -> str:
    if not args:
        return "Usage: block (number)\nExample: block 3 (after search or recent)"

    ref = args[0]
    if ref.isdigit() and int(ref) in _last_results:
        email_id = _last_results(int(ref))
    else:
        email_id = ref

    session = get_session()
    try:
        email = session.query(Email).filter(Email.id == email_id).first()
        if not email:
            return f"Email not found: {email_id}"

        from_address = email.front_address
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


def _unsubscribe(args: list[str]) -> str:
    if not args:
        return "Usage: unsubscribe (number)\nExample: unsubscribe 3 (after search or recent)"

    ref = args[0]
    if ref.isdigit() and int(ref) in _last_results:
        email_id = _last_results[int(ref)]
    else:
        email_id = ref

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
            # check for ?subject= param
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
        f"Unsubscribe header found but couldn't parse it for {from_name}.\n"
        f"You can block this sender instead: block {args[0]}"
    )


def _grammar(args: list[str]) -> str:
    if not args:
        return "Usage: grammar (text)\nExample: grammar I wants to meeting on tuesday"

    text = " ".join(args)

    prompt = _grammar_template.render(text=text)
    return llm.generate(prompt, json_mode=False)


def _ask(args: list[str], chat_id: str = "") -> str:
    if not args:
        return "Please provide a question.\nExample: ask What did John say about the budget?"

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
    parsed = _parse_json(raw)

    answer = parsed.get("answer", raw)
    confidence = parsed.get("confidence", "unknown")

    lines = [f"{answer}\n", f"Confidence: {confidence}", f"Sources ({len(results)}):"]
    for i, r in enumerate(results, 1):
        sender = r["from_name"] or r["from_address"]
        subject = r["subject"] or "(no subject)"
        lines.append(f"   [{i}] {subject} - {sender}")

    return "\n".join(lines)


def _chitchat(text: str, chat_id: str) -> str:
    try:
        history = chat_memory.get_recent(chat_id)
        history_text = chat_memory.format_for_prompt(history)

        prompt = _chitchat_template.render(
            message=text, conversation_history=history_text
        )

        return llm.generate(prompt, json_mode=False)
    except Exception as e:
        logger.error(f"Chitchat failed: {e}")
        return "...System nominal. How may I assist you, Operator?"


def _handle_draft(args: list[str]) -> str:
    if not args:
        return (
            "Usage: \n"
            "draft reply (email_id) (instructions)\n"
            "draft new (recipient) (instructions)"
        )

    action = args[0].lower()

    if action == "reply":
        return _draft_reply(args[1:])

    if action == "new":
        return _draft_new(args[1:])

    return f"Unknown draft action: {action}\n" "Usage: draft reply or draft new"


def _draft_reply(args: list[str]) -> str:
    global _last_draft_id

    if len(args) < 2:
        return (
            "Usage: draft reply (email_id) (instructions)\n"
            "Example: draft reply sviat_123abc agree but suggest Thursday"
        )

    email_id = args[0]

    # resolve number alias from last search/recent
    if email_id.isdigit() and int(email_id) in _last_results:
        email_id = _last_results[int(email_id)]

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
    parsed = _parse_json(raw)

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
        _last_draft_id = draft.id
    finally:
        session.close()

    return (
        f"Draft reply to: {from_name or from_address} (#{_last_draft_id})\n"
        f"Subject: {suggested_subject}\n"
        f"---\n"
        f"{reply_body}\n"
        f"---\n"
        f"Type 'send' to send this draft"
    )


def _draft_new(args: list[str]) -> str:
    global _last_draft_id

    if len(args) < 2:
        return (
            "Usage: draft new (recipient) (instructions)\n"
            "Example: draft new john@example.com ask about project deadline"
        )

    to_address = args[0]
    instructions = " ".join(args[1:])

    # try to find recipent name from the past emails
    recipient_name = None
    session = get_session()
    try:
        known = session.query(Email).filter(Email.from_address == to_address).first()
        if known:
            recipient_name = known.from_name
    finally:
        session.close()

    prompt = _draft_new_template.render(
        to_address=to_address,
        recipient_name=recipient_name,
        instructions=instructions,
    )

    raw = llm.generate(prompt)
    parsed = _parse_json(raw)

    email_body = parsed.get("email_body", raw)
    suggested_subject = parsed.get("suggested_subject", "")

    session = get_session()
    try:
        draft = Draft(
            account_id="sviatoslavbarbutsa@gmail.com",
            draft_type="new",
            to_address=to_address,
            subject=suggested_subject,
            body=email_body,
        )
        session.add(draft)
        session.commit()
        _last_draft_id = draft.id
    finally:
        session.close()

    return (
        f"Draft to: {to_address} (#{_last_draft_id})\n"
        f"Subject: {suggested_subject}\n"
        f"---\n"
        f"{email_body}\n"
        f"---\n"
        f"Type 'send' to send this draft"
    )


def _send_email(args: list[str]) -> str:
    global _last_draft_id

    # parse: "send", "send 3", "send at 14:30", "send 3 at 14:30", "send in 2h", "send 3 in 2h30m"
    draft_id = None
    time_args = []

    for i, arg in enumerate(args):
        if arg.lower() in ("at", "in"):
            time_args = args[i:]
            break
        elif arg.isdigit() and draft_id is None:
            draft_id = int(arg)

    if draft_id is None:
        if _last_draft_id:
            draft_id = _last_draft_id
        else:
            return (
                "No draft to send. Create one with 'draft reply' or 'draft new' first."
            )

    session = get_session()
    try:
        draft = session.query(Draft).filter(Draft.id == draft_id).first()
        if not draft:
            return f"Draft #{draft_id} not found."

        if draft.status == "sent":
            return f"Draft #{draft_id} was already sent."

        if draft.status == "scheduled":
            return (
                f"Draft #{draft_id} is already scheduled for "
                f"{draft.scheduled_at.strftime('%Y-%m-%d %H:%M')}.\n"
                f"Use 'schedule cancel {draft_id}' to cancel first."
            )

        # schedule send
        if time_args:
            send_at = _parse_time(time_args)
            if isinstance(send_at, str):
                # err messag
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

        # immediately send
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

        return f"Email sent!\n" f"To: {draft.to_address}\n" f"Subject: {draft.subject}"
    except Exception as e:
        logger.error(f"Send failed: {e}")
        return f"Failed to send draft #{draft_id}: {e}"
    finally:
        session.close()


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
            # if time already passed today so schedule it for tomorrow
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


def _handle_schedule(args: list[str]) -> str:
    if not args:
        return "Usage: schedule list | schedule cancel (draft_id)"

    action = args[0].lower()

    if action == "list":
        return _schedule_list()

    if action == "cancel":
        return _schedule_cancel(args[1:])

    return f"Unknown schedule action: {action}\nUsage: schedule list | schedule cancel (draft_id)"


def _schedule_list() -> str:
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
                f"   Send at: {time_str}"
            )

        return "\n\n".join(lines)
    finally:
        session.close()


def _schedule_cancel(args: list[str]) -> str:
    if not args or not args[0].isdigit():
        return "Usage: schedule cancel (draft_id)\nExample: schedule cancel 5"

    draft_id = int(args[0])

    session = get_session()
    try:
        draft = session.query(Draft).filter(Draft.id == draft_id).first()
        if not draft:
            return f"Draft #{draft_id} not found."

        if draft.status != "scheduled":
            return f"Draft #{draft_id} is not scheduled (status: {draft.status})."

        draft.status = "draft"
        draft.scheduled_at = None
        session.commit()

        return (
            f"Cancelled scheduled send for draft #{draft_id}.\n"
            f"Draft restored. Use 'send {draft_id}' to send immediatelly"
        )
    finally:
        session.close()


# dispatch table registry: intent name -> (handler_fn, param_keys)
# must move it below all the functiona otherwise they won't be defined if I put this thing at the top
INTENT_DISPATCH = {
    "import_start": (_import_start, ["account", "count"]),
    "import_pause": (_import_pause, ["account"]),
    "import_resume": (_import_resume, ["account"]),
    "import_status": (_import_status, []),
    "import_history": (_import_history, ["account"]),
    "draft_reply": (_draft_reply, ["email_id", "instructions"]),
    "draft_new": (_draft_new, ["recipient", "instructions"]),
    "send": (_send_email, ["draft_id"]),
    "schedule_list": (_schedule_list, []),
    "schedule_cancel": (_schedule_cancel, ["draft_id"]),
    "accounts": (_accounts_info, []),
    "search": (_search, ["query"]),
    "show_email": (_show_email, ["number"]),
    "delete": (_delete_email, ["number"]),
    "block": (_block_sender, ["number"]),
    "unsubscribe": (_unsubscribe, ["number"]),
    "grammar": (_grammar, ["text"]),
    "ask": (_ask, ["question"]),
    "recent": (_recent, ["count"]),
    "help": (lambda: HELP_TEXT, []),
}


def _llm_route(text: str, chat_id: str) -> str:
    try:
        prompt = _classify_template.render(message=text)
        raw = llm.generate(prompt)
        parsed = _parse_json(raw)

        intent = parsed.get("intent", "chitchat")
        params = parsed.get("params", {})

        logger.info(f"LLM route: intent={intent}, params={params}")

        if intent == "chitchat":
            return _chitchat(text, chat_id)

        if intent not in INTENT_DISPATCH:
            return _chitchat(text, chat_id)

        handler, param_keys = INTENT_DISPATCH[intent]

        args = []
        for key in param_keys:
            if key in params and params[key] is not None:
                args.append(str(params[key]))

        if intent == "ask":
            return handler(args, chat_id)
        if param_keys:
            return handler(args)
        return handler()

    except Exception as e:
        logger.error(f"LLM routing failed: {e}")
        return "Sorry, I couldn't understand that. Type 'help' for available commands."
