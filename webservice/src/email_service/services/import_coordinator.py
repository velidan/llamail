import logging
from datetime import datetime

from email_service.models.database import ImportJob, ImportTask, get_session
from email_service.services import gmail_client

logger = logging.getLogger(__name__)


def create_job(account_id: str, max_emails: int = 0) -> ImportJob:
    service = gmail_client.get_gmail_service()

    logger.info(f"Discovering emails from {account_id}...")
    gmail_ids = gmail_client.list_message_ids(service, max_results=max_emails)
    logger.info(f"Found {len(gmail_ids)} emails")

    session = get_session()
    try:
        job = ImportJob(
            account_id=account_id, status="pending", total_emails=len(gmail_ids)
        )
        session.add(job)
        session.flush()

        existing = {
            row[0]
            for row in session.query(ImportTask.gmail_id)
            .filter(ImportTask.gmail_id.in_(gmail_ids))
            .all()
        }

        new_count = 0
        for gmail_id in gmail_ids:
            if gmail_id in existing:
                continue
            task = ImportTask(job_id=job.id, gmail_id=gmail_id, status="pending")
            session.add(task)
            new_count += 1

        job.skipped_count = len(gmail_ids) - new_count
        session.commit()

        logger.info(
            f"Job {job.id}: {new_count} new tasks, {job.skipped_count} already imported"
        )
        return job

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_job_status(job_id: int) -> dict:
    session = get_session()
    try:
        job = session.query(ImportJob).filter(ImportJob.id == job_id).first()
        if not job:
            return None

        return {
            "job_id": job.id,
            "account_id": job.account_id,
            "status": job.status,
            "total_emails": job.total_emails,
            "processed_count": job.processed_count,
            "failed_count": job.failed_count,
            "skipped_count": job.skipped_count,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }
    finally:
        session.close()


def pause_job(job_id: int):
    session = get_session()
    try:
        job = session.query(ImportJob).filter(ImportJob.id == job_id).first()
        if job and job.status == "running":
            job.status = "paused"
            session.commit()
    finally:
        session.close()


def list_all_jobs() -> list[dict]:
    session = get_session()
    try:
        jobs = session.query(ImportJob).order_by(ImportJob.id.desc()).all()
        return [
            {
                "job_id": job.id,
                "account_id": job.account_id,
                "status": job.status,
                "total_emails": job.total_emails,
                "processed_count": job.processed_count,
                "failed_count": job.failed_count,
                "skipped_count": job.skipped_count,
                "created_at": job.created_at,
                "finished_at": job.finished_at,
            }
            for job in jobs
        ]
    finally:
        session.close()


def resume_job(account_id: str) -> dict | None:
    session = get_session()
    try:
        job = (
            session.query(ImportJob)
            .filter(
                ImportJob.account_id == account_id,
                ImportJob.status.in_(["paused", "completed"]),
            )
            .order_by(ImportJob.id.desc())
            .first()
        )
        if not job:
            return None

        reset_count = (
            session.query(ImportTask)
            .filter(ImportTask.job_id == job.id, ImportTask.status == "failed")
            .update({"status": "pending", "error": None, "retries": 0})
        )

        job.failed_count = 0
        job.status = "pending"
        session.commit()

        return {"job_id": job.id, "reset_count": reset_count}
    finally:
        session.close()


def find_active_job(acocunt_id: str) -> int | None:
    session = get_session()
    try:
        job = (
            session.query(ImportJob)
            .filter(ImportJob.account_id == acocunt_id, ImportJob.status == "running")
            .order_by(ImportJob.id.desc())
            .first()
        )
        return job.id if job else None
    finally:
        session.close()
