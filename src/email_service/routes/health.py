from fastapi import APIRouter
from sqlalchemy import text

from email_service.models.database import get_session
from email_service.models.schemas import HealthResponse
from email_service.services import llm, embeddings

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check():
    llm_ok = llm.is_available()
    chroma_ok = embeddings.is_available()

    db_ok = False
    try:
        session = get_session()
        session.execute(text("SELECT 1"))
        db_ok = True
        session.close()
    except Exception:
        pass

    all_ok = llm_ok and db_ok and chroma_ok

    return HealthResponse(
        status="ok" if (all_ok) else "degraded",
        llm=llm_ok,
        database=db_ok,
        chromadb=chroma_ok,
    )
