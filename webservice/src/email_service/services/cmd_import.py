"""
Imoprt & account commands for TElegram handler
"""

import logging
import threading

from email_service.services import import_coordinator, gmail_client, import_worker
from email_service.models.database import get_session
from email_service.config import settings

logger = logging.getLogger(__name__)


def handle_import(args: list[str]) -> str:
    if not args:
        return "Please specify an action:"
    action = args[0].lower()

    if action == "status":
        return import_status()

    if action == "start":
        return import_start(args[1:])

    if action == "pause":
        return import_pause(args[1:])

    if action == "resume":
        return import_resume(args[1:])

    if action == "history":
        return import_history(args[1:])

    return f"Unknown imoprt acction: {action}\nPlease specify an action: start, pause, resume, status, or history"


def import_status() -> str:
    jobs = import_coordinator.list_all_jobs()
    if not jobs:
        return "No import jobs found."

    latest = {}
    for job in jobs:
        acct = job["account_id"]
        if acct not in latest:
            latest[acct] = job

    session = get_session()
    try:
        from email_service.models.database import Email
        from sqlalchemy import func

        account_counts = dict(
            session.query(Email.account_id, func.count(Email.id))
            .group_by(Email.account_id)
            .all()
        )
    finally:
        session.close()

    lines = ["Import status:\n"]
    for acct, job in latest.items():
        total = job["total_emails"]
        done = job["processed_count"]
        failed = job["failed_count"]
        skipped = job["skipped_count"]
        total_in_db = account_counts.get(acct, 0)

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
            f"      Total in DB: {total_in_db} | This job: {done} new, {skipped} already imported, {failed} failed ({pct}%)"
        )
    return "\n".join(lines)


def import_start(args: list[str]) -> str:
    if not args:
        return "Please provice the account email.\nExample: import start user@gmail.com"

    account_id = args[0]
    max_emails = 0
    if len(args) > 1 and args[1].lower() != "all":
        try:
            max_emails = int(args[1])
        except ValueError:
            return f"Invalid count: {args[1]}. Must be a number or 'all'"

    try:
        job = import_coordinator.create_job(account_id, max_emails)

        thread = threading.Thread(
            target=import_worker.run_job, args=(job.id,), daemon=True
        )
        thread.start()

        return (
            f"Import started!\n"
            f"Account: {account_id}\n"
            f"Emails found: {job.total_emails}\n"
            f"New to process: {job.total_emails - job.skipped_count}\n"
            f"Skipped (already imoprt): {job.skipped_count}"
        )
    except Exception as e:
        logger.error(f"Import start failed: {e}")
        return f"Failed to start import: {e}"


def import_pause(args: list[str]) -> str:
    if not args:
        return "Please provide the account email.\nExample: import pause user@gmail.com"

    account_id = args[0]
    job_id = import_coordinator.find_active_job(account_id)

    if not job_id:
        return f"No running import found for {account_id}"

    import_coordinator.pause_job(job_id)
    return f"Import paused for {account_id}"


def import_resume(args: list[str]) -> str:
    if not args:
        return (
            "Please provide the account email.\nExample: import resume user@gmail.com"
        )

    account_id = args[0]
    result = import_coordinator.resume_job(account_id)

    if not result:
        return f"No paused or completed import found for {account_id}"

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


def import_history(args: list[str]) -> str:
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


def account_info() -> str:
    try:
        service = gmail_client.get_gmail_service()
        total = gmail_client.get_total_messages(service)

        jobs = import_coordinator.list_all_jobs()
        imported = 0
        for job in jobs:
            imported += job["processed_count"]

        return (
            f"Connected account:\n\n"
            f"{settings.default_account}\n"
            f"    Total emails on server: {total}\n"
            f"    Imported so far: {imported}"
        )
    except Exception as e:
        return f"Failed to get account info: {e}"
