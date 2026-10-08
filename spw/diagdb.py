from __future__ import annotations

import sqlite3
from pathlib import Path

from spw.gates.engine import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS diag_events(
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn INT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    context TEXT NOT NULL,
    commit_line TEXT NOT NULL,
    safeguard_line TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_diag_events_session ON diag_events(session_id, id);
"""


def append_events(db_path: str | Path, session_id: str, turn: int, events: list[Event]) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO diag_events(session_id,turn,context,commit_line,safeguard_line,detail) VALUES(?,?,?,?,?,?)",
            [(session_id, turn, e.context, f"COMMIT: {e.commit}", f"SAFEGUARD: {e.safeguard}", e.detail) for e in events],
        )


def read_events(db_path: str | Path, session_id: str) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM diag_events WHERE session_id=? ORDER BY id", (session_id,))]
