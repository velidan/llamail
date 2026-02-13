import logging
from datetime import datetime

from email_service.config import settings
from email_service.models.database import ImportJob, ImportTask, get_session
from email_service.models.schemas import ProcessEmailRequest
from email_service.services import gmail_client, email_processor

logger = logging.getLogger(__name__)


def run_job(job_id: int):
    service = gmail_client.get_gmail_service()

    session = get_session()
    try:
        job = session.query(ImportJob).filter(ImportJob.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found")
            return
        job.status = "running"
        job.started_at = datetime.now()
        job.last_heartbeat = datetime.now()
        session.commit()
    finally:
        session.close()

    logger.info(f"Job {job_id}: starting worker")

    while True:
        session = get_session()
        try:
            job = session.query(ImportJob).filter(ImportJob.id == job_id).first()
            if job.status == "paused":
                logger.info(f"Job {job_id}: paused")
                return

            task = (
                session.query(ImportTask)
                .filter(
                    ImportTask.job_id == job_id,
                    ImportTask.status.in_(["pending", "processing"]),
                )
                .order_by(ImportTask.id)
                .first()
            )

            if not task:
                job.status = "completed"
                job.finished_at = datetime.now()
                session.commit()
                logger.info(f"Job {job_id}: completed")
                return

            task.status = "processing"
            session.commit()
            task_id = task.id
            gmail_id = task.gmail_id
            account_id = job.account_id

        finally:
            session.close()

        _update_heartbeat(job_id)
        _process_task(service, job_id, task_id, gmail_id, account_id)


def _process_task(service, job_id: int, task_id: int, gmail_id: str, account_id: str):
    try:
        email_data = gmail_client.fetch_email(service, gmail_id)

        request = ProcessEmailRequest(
            account_id=account_id,
            **email_data,
        )

        result = email_processor.process_email(request)
        logger.info(f"Taskk {task_id}: {gmail_id} -> {result.category}")

        _update_task(task_id, "done")
        _update_job_counter(job_id, "processed_count")
    except Exception as e:
        logger.error(f"Task {task_id}: {gmail_id} failed: {e}")
        _handle_failure(task_id, job_id, str(e))


def _update_task(task_id: int, status: str, error: str = None):
    session = get_session()
    try:
        task = session.query(ImportTask).filter(ImportTask.id == task_id).first()
        task.status = status
        task.error = error
        task.processed_at = datetime.now()
        session.commit()
    finally:
        session.close()


def _update_job_counter(job_id: int, counter: str):
    session = get_session()
    try:
        job = session.query(ImportJob).filter(ImportJob.id == job_id).first()
        setattr(job, counter, getattr(job, counter) + 1)
        session.commit()
    finally:
        session.close()


def _handle_failure(task_id: int, job_id: int, error: str):
    session = get_session()
    try:
        task = session.query(ImportTask).filter(ImportTask.id == task_id).first()
        task.retries += 1

        if task.retries >= settings.import_max_retries:
            task.status = "failed"
            task.error = error
            task.processed_at = datetime.now()
            _update_job_counter(job_id, "failed_count")
        else:
            task.status = "pending"

        session.commit()
    finally:
        session.close()


def _update_heartbeat(job_id: int):
    session = get_session()
    try:
        job = session.query(ImportJob).filter(ImportJob.id == job_id).first()
        job.last_heartbeat = datetime.now()
        session.commit()
    finally:
        session.close()
