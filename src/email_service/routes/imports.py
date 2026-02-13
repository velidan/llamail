import threading
import logging

from fastapi import APIRouter, HTTPException

from email_service.services import import_coordinator, import_worker

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/import")
def start_import(account_id: str, max_emails: int = 500):
    job = import_coordinator.create_job(account_id, max_emails)

    thread = threading.Thread(target=import_worker.run_job, args=(job.id,), daemon=True)
    thread.start()

    return {"job_id": job.id, "total_emails": job.total_emails, "status": "started"}


@router.get("/import/{job_id}")
def get_import_status(job_id: int):
    status = import_coordinator.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job not found")
    return status


@router.post("/import/{job_id}/pause")
def pause_import(job_id: int):
    import_coordinator.pause_job(job_id)
    return {"status": "paused"}
