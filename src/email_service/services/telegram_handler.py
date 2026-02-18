import logging

from email_service.services import import_coordinator, gmail_client
from email_service.services.search import hybrid_search
from email_service.models.database import Email, get_session

from email_service.services import llm
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

HELP_TEXT = """Available commands:

search <query>
ask <question>
recent [count]
draft reply <email_id> <instructions>
draft new <recipient> <instructions>
import start {account} [count|all]
import pause {account}
import resume {account}
import retry {account}
import status
import history {account}
accounts
help"""

CHITCHAT_RESPONSES = [
    "I'm here to help with your emails. Type 'help' to see what I can do."
]
# it's a registry for number aliases for emails eg {1: "full_email_id", 2: "full_email_id", ...}. Just don't want to type all the email ids to reply etc.
_last_results: dict[int, str] = {}


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
    elif command == "ask":
        reply = _ask(parts[1:], chat_id_str)
    elif command == "draft":
        reply = _handle_draft(parts[1:])
    else:
        reply = _llm_route(text, chat_id_str)

    chat_memory.save_message(chat_id_str, "assistant", reply)
    return reply


def _handle_import(args: list[str]) -> str:
    if not args:
        return (
            "Please specify an action: start, pause, resume, status, history, or retry"
        )
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

    if action == "retry":
        return _import_retry(args[1:])

    return f"Unknown import action: {action}\nPlease specify an action: start, pause, resume, status, history, or retry"


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
    job_id = import_coordinator.find_paused_job(account_id)

    if not job_id:
        return f"No paused import found for {account_id}"

    success = import_coordinator.resume_job(job_id)
    if not success:
        return f"Failed to resume import for {account_id}"

    import threading
    from email_service.services import import_worker

    thread = threading.Thread(target=import_worker.run_job, args=(job_id,), daemon=True)
    thread.start()

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


def _import_retry(args: list[str]) -> str:
    if not args:
        return "Please provide the account email.\nExample: import retry user@gmail.com"

    account_id = args[0]
    result = import_coordinator.retry_failed_tasks(account_id)

    if not result:
        return f"No failed imports found for {account_id}"

    import threading
    from email_service.services import import_worker

    thread = threading.Thread(
        target=import_worker.run_job, args=(result["job_id"],), daemon=True
    )
    thread.start()

    return (
        f"Retrying {result['reset_count']} failed emails\n"
        f"Account: {account_id}\n"
        f"Job #{result['job_id']} restarted"
    )


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
            "draft reply <email_id> <instructions>\n"
            "draft new <recipient> <instructions>"
        )

    action = args[0].lower()

    if action == "reply":
        return _draft_reply(args[1:])

    if action == "new":
        return _draft_new(args[1:])

    return f"Unknown draft action: {action}\n" "Usage: draft reply or draft new"


def _draft_reply(args: list[str]) -> str:
    if len(args) < 2:
        return (
            "Usage: draft reply <email_id> <instructions>\n"
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
    finally:
        session.close()

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

    return (
        f"Draft reply to: {from_name or from_address}\n"
        f"Subject: {suggested_subject}\n"
        f"---\n"
        f"{reply_body}"
    )


def _draft_new(args: list[str]) -> str:
    if len(args) < 2:
        return (
            "Usage: draft new <recipient> <instructions>\n"
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

    return (
        f"Draft to: {to_address}\n"
        f"Subject: {suggested_subject}\n"
        f"---\n"
        f"{email_body}"
    )


# dispatch table registry: intent name -> (handler_fn, param_keys)
# must move it below all the functiona otherwise they won't be defined if I put this thing at the top
INTENT_DISPATCH = {
    "import_start": (_import_start, ["account", "count"]),
    "import_pause": (_import_pause, ["account"]),
    "import_resume": (_import_resume, ["account"]),
    "import_retry": (_import_retry, ["account"]),
    "import_status": (_import_status, []),
    "import_history": (_import_history, ["account"]),
    "draft_reply": (_draft_reply, ["email_id", "instructions"]),
    "draft_new": (_draft_new, ["recipient", "instructions"]),
    "accounts": (_accounts_info, []),
    "search": (_search, ["query"]),
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
