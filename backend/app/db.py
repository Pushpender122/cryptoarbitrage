"""Database engine, session factory and schema bootstrap."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base, RiskSettings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _ensure_columns() -> None:
    """Add columns introduced after a database was first created."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "orders" not in tables:
        return
    existing = {column["name"] for column in inspector.get_columns("orders")}
    timestamp = "TIMESTAMP" if engine.dialect.name == "postgresql" else "DATETIME"
    additions = {
        "exchange_state": "VARCHAR(32)",
        "last_synced_at": timestamp,
    }
    with engine.begin() as connection:
        for name, ddl in additions.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE orders ADD COLUMN {name} {ddl}"))


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_columns()
    with SessionLocal() as db:
        if db.get(RiskSettings, 1) is None:
            db.add(RiskSettings(id=1))
            db.commit()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
