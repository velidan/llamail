import logging

from email_service.services import import_coordinator, gmail_client

logger = logging.getLogger(__name__)

HELP_TEXT = """Available commands:

import start <account> [count|all]
import pause <account>
import resume <account>
import retry <account>
import status
import history <account>
accounts
help"""


def handle_command(text: str) -> str:
    text = text.strip()
    if not text:
        return "Empty message. Type 'help' for commands."

    parts = text.split()
    command = parts[0].lower()

    if command == "help":
        return HELP_TEXT

    if command == "accounts":
        return _accounts_info()

    if command == "import":
        return _handle_import(parts[1:])

    return f"Unknown command: {command}\nType 'help' for available commands."


def _handle_import(args: list[str]) -> str:
    if not args:
        return "Usage: import <start|pause|resume|status>"
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

    return f"Unknown import action {action}\nUsage: import <start|pause|resume|status>"


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
        return "Usage: import start <account_email> [count|all]\nExample: import start user@gmail.com"

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
        return "Usage: import pause <account_email>"

    account_id = args[0]
    job_id = import_coordinator.find_active_job(account_id)

    if not job_id:
        return f"No running import found for {account_id}"

    import_coordinator.pause_job(job_id)
    return f"Import paused for {account_id}"


def _import_resume(args: list[str]) -> str:
    if not args:
        return "Usage: import resume <account_email>"

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
        return "Usage: import history <account_email>"

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
        return "Usage: import retry <account_email>"

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
