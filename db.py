"""SQLite state store for dedupe and the weekly recap."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS nudges (
    thread_id           TEXT PRIMARY KEY,
    recipient_email     TEXT NOT NULL,
    recipient_name      TEXT,
    subject             TEXT NOT NULL,
    outreach_mailing_id TEXT NOT NULL,
    open_count_at_nudge INTEGER NOT NULL,
    nudged_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS outreach_cursor (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    last_updated_at TIMESTAMP NOT NULL
);
"""


@dataclass
class NudgeRow:
    thread_id: str
    recipient_email: str
    recipient_name: str | None
    subject: str
    outreach_mailing_id: str
    open_count_at_nudge: int
    nudged_at: datetime


def init_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(
        db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def already_nudged(db_path: Path, thread_id: str) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM nudges WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return row is not None


def record_nudge(
    db_path: Path,
    *,
    thread_id: str,
    recipient_email: str,
    recipient_name: str | None,
    subject: str,
    outreach_mailing_id: str,
    open_count_at_nudge: int,
) -> bool:
    """Insert a nudge row. Returns False if the thread was already nudged
    (the INSERT OR IGNORE hit the unique constraint)."""
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO nudges (
                thread_id, recipient_email, recipient_name, subject,
                outreach_mailing_id, open_count_at_nudge
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                thread_id,
                recipient_email,
                recipient_name,
                subject,
                outreach_mailing_id,
                open_count_at_nudge,
            ),
        )
        return cursor.rowcount == 1


def nudges_since(db_path: Path, since: datetime) -> list[NudgeRow]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT thread_id, recipient_email, recipient_name, subject,
                   outreach_mailing_id, open_count_at_nudge, nudged_at
            FROM nudges
            WHERE nudged_at >= ?
            ORDER BY nudged_at DESC
            """,
            (since,),
        ).fetchall()
    return [
        NudgeRow(
            thread_id=r["thread_id"],
            recipient_email=r["recipient_email"],
            recipient_name=r["recipient_name"],
            subject=r["subject"],
            outreach_mailing_id=r["outreach_mailing_id"],
            open_count_at_nudge=r["open_count_at_nudge"],
            nudged_at=datetime.fromisoformat(r["nudged_at"])
            if isinstance(r["nudged_at"], str)
            else r["nudged_at"],
        )
        for r in rows
    ]


def get_cursor(db_path: Path) -> datetime | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_updated_at FROM outreach_cursor WHERE id = 1"
        ).fetchone()
    if row is None:
        return None
    value = row["last_updated_at"]
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def set_cursor(db_path: Path, last_updated_at: datetime) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO outreach_cursor (id, last_updated_at) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_updated_at = excluded.last_updated_at
            """,
            (last_updated_at,),
        )
