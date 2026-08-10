from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spw import DEFAULT_DB_PATH
from spw.parse import ensure_schema


def add_verdict(
    part: str,
    method: str,
    cluster_id: str,
    result: str,
    notes: str = "",
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    with sqlite3.connect(Path(db_path)) as conn:
        ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO verdicts(date, part, method, cluster_id, result, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (datetime.now(UTC).date().isoformat(), part, method, cluster_id, result, notes),
        )
        conn.commit()


def report_verdicts(db_path: str | Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with sqlite3.connect(Path(db_path)) as conn:
        ensure_schema(conn)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT method, cluster_id, result, COUNT(*) AS total
            FROM verdicts
            GROUP BY method, cluster_id, result
            ORDER BY method, cluster_id, result
            """
        ).fetchall()
    return [dict(row) for row in rows]
