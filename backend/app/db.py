import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./.data/learningmachine-dev.db")


def make_engine(database_url: str | None = None):
    url = database_url or _database_url()
    if url.startswith("sqlite:///"):
        local_path = url.removeprefix("sqlite:///")
        if local_path != ":memory:" and not local_path.startswith("/"):
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    options = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=options, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
