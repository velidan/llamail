from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    ForeignKey,
    String,
    Text,
    create_engine,
    event,
    text,
)

from sqlalchemy.orm import DeclarativeBase, sessionmaker
from email_service.config import settings


class Base(DeclarativeBase):
    pass


class Email(Base):
    __tablename__ = "emails"

    # "{account_id}_{gmail_id}"
    id = Column(String, primary_key=True)
    account_id = Column(String, nullable=False, index=True)
    gmail_id = Column(String, nullable=False)
    thread_id = Column(String)

    # sender / recipientos
    from_address = Column(String, nullable=False, index=True)
    from_name = Column(String)
    to_addresses = Column(Text)
    cc_addresses = Column(Text)

    # content
    subject = Column(String)
    body_text = Column(Text)
    snippet = Column(String)
    received_at = Column(DateTime, nullable=False, index=True)

    # llm generated data
    summary = Column(Text)
    # work, personal, spam, blah blah
    category = Column(String)
    priority = Column(String)
    # positive , negative, urgent, neutral
    sentiment = Column(String)
    action_required = Column(Boolean, default=False)
    action_items = Column(Text)  # json str
    key_people = Column(Text)  # json str

    # embedding status
    has_embedding = Column(Boolean, default=False)
    is_chunked = Column(Boolean, default=False)
    chunk_count = Column(Integer, default=0)

    processed_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)


class EmailChunk(Base):
    __tablename__ = "email_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email_id = Column(
        String,
        ForeignKey("emails.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index = Column(Integer, nullable=False)

    content = Column(Text, nullable=False)
    token_count = Column(Integer)
    start_char = Column(Integer)
    end_char = Column(Integer)
    summary = Column(Text)
    has_embedding = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.now)


# --- Engine & sesion ---
def get_engine():
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{settings.db_path}", echo=False)

    # enable WAL mode for better concurrent read/write
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_tables(engine):
    Base.metadata.create_all(engine)

    # create fts virt table (SQLALchemy can't do this declaratively)
    with engine.connect() as conn:
        conn.execute(
            text(
                """
            CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
                subject, body_text, summary, from_name, from_address,
                content='emails', content_rowid='rowid'
                          )
                          """
            )
        )
        conn.commit()


SessionLocal = None


def init_db():
    global SessionLocal
    engine = get_engine()
    create_tables(engine)
    SessionLocal = sessionmaker(bind=engine)
    return engine


def get_session():
    return SessionLocal()
