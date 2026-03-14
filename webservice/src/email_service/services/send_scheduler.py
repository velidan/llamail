import logging
import threading
import time
from datetime import datetime

from email_service.config import settings
from email_service.models.database import Draft, get_session
from email_service.services import gmail_client, telegram_notifier


logger = logging.getLogger(__name__)

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def start_scheduler():
    global _thread
    if _thread and _thread.is_alive():
        logger.warning("Scheduler already running")
        return

    _stop_event.clear()
    _thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _thread.start()
    logger.info(
        "Send scheduler started (interval: %ds)", settings.scheduler_check_interval
    )


def stop_scheduler():
    _stop_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
    logger.info("Send scheduler stopped")


def _scheduler_loop():
    while not _stop_event.is_set():
        try:
            _process_due_drafts()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")

        # the right way. time.sleep(30) is bad because if the stop_scheduler will be called it still wait 30 sec and the wait method will be stopped immediatelly
        _stop_event.wait(timeout=settings.scheduler_check_interval)


def _process_due_drafts():
    session = get_session()
    try:
        now = datetime.now()
        due_drafts = (
            session.query(Draft)
            .filter(
                Draft.status == "scheduled",
                Draft.scheduled_at <= now,
            )
            .all()
        )

        if not due_drafts:
            return

        service = gmail_client.get_gmail_service()

        for draft in due_drafts:
            try:
                gmail_client.send_email(
                    service,
                    to=draft.to_address,
                    subject=draft.subject or "",
                    body=draft.body,
                    thread_id=draft.thread_id,
                )
                draft.status = "sent"
                logger.info(f"Scheduled draft #{draft.id} sent to {draft.to_address}")
                telegram_notifier.notify(
                    f"Scheduled email sent!\n"
                    f"To: {draft.to_address}\n"
                    f"Subject: {draft.subject or '(no subject)'}"
                )
            except Exception as e:
                logger.error(f"failed to send scheduled draft #{draft.id}: {e}")

        session.commit()
    finally:
        session.close()
