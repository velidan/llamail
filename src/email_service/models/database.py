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


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    total_emails = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    # it's a protection from a deadworker. It helps to understand the case when the worker died and the job is stopped but being at running status. Later on startup it will be checked and if it's running but hearbeat was say 5 or 10 min ago - it's dead and should reset the status to paused
    last_heartbeat = Column(DateTime)

    created_at = Column(DateTime, default=datetime.now)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)


class ImportTask(Base):
    __tablename__ = "import_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(
        Integer,
        ForeignKey("import_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    gmail_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    error = Column(Text)
    retries = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.now)
    processed_at = Column(DateTime)


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

        # --- triggers to keep FTS5 in sync with emails table --0
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN INSERT INTO emails_fts(rowid, subject, body_text, summary, from_name, from_address) VALUES (new.rowid, new.subject, new.body_text, new.summary, new.from_name, new.from_address);
                END;
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN INSERT INTO emails_fts(emails_fts, rowid, subject, body_text, summary, from_name, from_address) VALUES ('delete', old.rowid, old.subject, old.body_text, old.summary, old.from_name, old.from_address);
                END;
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN INSERT INTO emails_fts(emails_fts, rowid, subject, body_text, summary, from_name, from_address) VALUES ('delete', old.rowid, old.subject, old.body_text, old.summary, old.from_name, old.from_address);
                INSERT INTO emails_fts(rowid, subject, body_text, summary, from_name, from_address) VALUES (new.rowid, new.subject, new.body_text, new.summary, new.from_name, new.from_address);
                END;
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

def rebuild_fts():
    """Backfill FTS5 index from existing emails. One-time catch-up."""
    session = get_session()
    try:
        session.execute(text("INSERT INTO emails_fts(emails_fts) VALUES ('rebuild')"))
        session.commit()
    finally:
        session.close()
