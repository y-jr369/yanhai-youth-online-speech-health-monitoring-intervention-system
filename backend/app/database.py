import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "datasets" / "runtime" / "runtime_showcase_demo_v2.db"
DATABASE_PATH = Path(os.getenv("MONITORING_DB_PATH", str(DEFAULT_DATABASE_PATH))).resolve()
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"


def _ensure_database_ready() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DATABASE_PATH.exists() and not os.access(DATABASE_PATH, os.W_OK):
        raise RuntimeError(f"Database file is not writable: {DATABASE_PATH}")
    if not os.access(DATABASE_PATH.parent, os.W_OK):
        raise RuntimeError(f"Database directory is not writable: {DATABASE_PATH.parent}")


_ensure_database_ready()

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
