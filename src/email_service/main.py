import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI

from email_service.models.database import init_db, get_session, ImportJob
from email_service.services.embeddings import init_vectorstore
from email_service.routes import health, process, imports, telegram
from email_service.services import send_scheduler
from email_service.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # the app is used automatically by fastapi. Lifespan MUST accept this.
    # however, if needed here it's possible to add to it some shared state that will be shared accross the whole components eg app.state.start_time = time.time()
    logger.info("Starting Emails Service...")
    init_db()
    init_vectorstore()
    recover_stale_jobs()
    send_scheduler.start_scheduler()
    logger.info("Email Service ready!")
    yield
    logger.info("Shutting down Email Service...")
    send_scheduler.stop_scheduler()


def recover_stale_jobs():
    session = get_session()
    try:
        stale_cutoff = datetime.now() - timedelta(minutes=5)
        stale_jobs = (
            session.query(ImportJob)
            .filter(
                ImportJob.status == "running",
                (ImportJob.last_heartbeat < stale_cutoff)
                | (ImportJob.last_heartbeat == None),
            )
            .all()
        )
        for job in stale_jobs:
            logger.info(f"Recovering stale job {job.id} (account: {job.account_id})")
            job.status = "paused"
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Email Intelligence Service", version="2.0.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(process.router)
app.include_router(imports.router)
app.include_router(telegram.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
