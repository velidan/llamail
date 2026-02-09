import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from email_service.models.database import init_db
from email_service.services.embeddings import init_vectorstore
from email_service.routes import health, process
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
    logger.info("Email Service ready!")
    yield
    logger.info("Shutting down Email Service...")


app = FastAPI(title="Email Intelligence Service", version="2.0.0", lifespan=lifespan)

app.include_router(health.router)
app.include_router(process.router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port)
