from collections.abc import Generator
from sqlalchemy.orm import Session
from email_service.models.database import get_session


def get_db() -> Generator[Session]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()
