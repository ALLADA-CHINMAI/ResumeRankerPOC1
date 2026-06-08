"""
SQL database helpers for JD metadata.
Auto-generates 4-digit req IDs; stores JD file bytes in jd_blob column.
Table is created automatically on first connection if it does not exist.
"""

import os
from datetime import datetime, timezone
from typing import List, Dict, Tuple

from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, String, LargeBinary, DateTime
from sqlalchemy.orm import DeclarativeBase, Session

load_dotenv()

_DB_CONN = os.getenv("DB_CONNECTION_STRING")
_engine = None


class _Base(DeclarativeBase):
    pass


class JDRecord(_Base):
    __tablename__ = "jd_metadata"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    jd_name    = Column(String(255), nullable=False)
    jd_blob    = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    @property
    def req_id(self) -> str:
        return f"{self.id:04d}"


def get_engine():
    global _engine
    if _engine is None:
        if not _DB_CONN:
            raise RuntimeError(
                "DB_CONNECTION_STRING is not set. Add it to your .env file.\n"
                "Example (Azure SQL): mssql+pyodbc://user:pass@server.database.windows.net/db"
                "?driver=ODBC+Driver+18+for+SQL+Server"
            )
        _engine = create_engine(_DB_CONN, pool_pre_ping=True)
    return _engine


def insert_jd(jd_name: str, jd_blob: bytes) -> str:
    """Persist a new JD record and return its zero-padded 4-digit req_id."""
    now = datetime.now(timezone.utc)
    with Session(get_engine()) as session:
        record = JDRecord(
            jd_name=jd_name, jd_blob=jd_blob,
            created_at=now, updated_at=now,
        )
        session.add(record)
        session.commit()
        session.refresh(record)
        return record.req_id


def list_jds() -> List[Dict]:
    """Return [{req_id, jd_name}, ...] ordered by req_id ascending."""
    with Session(get_engine()) as session:
        records = session.query(JDRecord).order_by(JDRecord.id).all()
        return [{"req_id": r.req_id, "jd_name": r.jd_name} for r in records]


def fetch_jd_by_req_id(req_id: str) -> Tuple[str, bytes]:
    """Return (jd_name, jd_blob_bytes) for the given req_id.

    Raises:
        ValueError: if req_id is invalid or no matching JD exists.
    """
    try:
        jd_id = int(req_id)
    except ValueError:
        raise ValueError(
            f"Invalid req_id '{req_id}'. Expected a 4-digit number like '0001'."
        )
    with Session(get_engine()) as session:
        record = session.get(JDRecord, jd_id)
        if record is None:
            raise ValueError(f"No JD found with req_id '{req_id}'.")
        return record.jd_name, bytes(record.jd_blob)
