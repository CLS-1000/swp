from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spw import DEFAULT_DB_PATH
from spw.parse import ensure_schema

FLOAT_RE = re.compile(r"\d+(?:\.\d+)?")


class ValidationError(RuntimeError):
    pass


def _first_wheelbase(value: str) -> float | None:
    match = FLOAT_RE.search(value or "")
    return float(match.group(0)) if match else None


def _drive_set(value: str) -> set[str]:
    return {part.strip() for part in (value or "").split(",") if part.strip()}


def _cluster_rows(conn: sqlite3.Connection, cluster_id: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(
        """
        SELECT v.*, m.confidence
        FROM membership m
        JOIN vehicles v ON v.id = m.vehicle_id
        WHERE m.cluster_id = ?
        ORDER BY v.id
        """,
        (cluster_id,),
    )
    return list(cursor.fetchall())


def _consistency_failures(rows: list[sqlite3.Row]) -> list[str]:
    reasons: list[str] = []
    constructions = {row["construction"] for row in rows if row["construction"]}
    if len(constructions) > 1:
        reasons.append("construction mismatch")
    drive_sets = [_drive_set(row["drive"]) for row in rows if _drive_set(row["drive"])]
    if drive_sets:
        shared = set.intersection(*drive_sets)
        if not shared:
            reasons.append("no shared drive-wheel intersection")
    wheelbases = [wb for wb in (_first_wheelbase(row["wheelbase"]) for row in rows) if wb is not None]
    if wheelbases and max(wheelbases) - min(wheelbases) > 1.0:
        reasons.append("wheelbase spread exceeds 1.0 in")
    start = max(row["year_start"] for row in rows)
    end = min(row["year_end"] for row in rows)
    if start > end:
        reasons.append("no production-year overlap")
    return reasons


def validate_database(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    db_file = Path(db_path)
    with sqlite3.connect(db_file) as conn:
        ensure_schema(conn)
        duplicate_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT v.make, v.model, v.styles, v.year_start, v.year_end, m.cluster_id, COUNT(*) AS c
                FROM membership m
                JOIN vehicles v ON v.id = m.vehicle_id
                GROUP BY v.make, v.model, v.styles, v.year_start, v.year_end, m.cluster_id
                HAVING c > 1
            )
            """
        ).fetchone()[0]
        if duplicate_count:
            raise ValidationError(f"duplicate membership tuples detected: {duplicate_count}")
        conn.execute(
            """
            UPDATE membership
            SET confidence = 'edc'
            WHERE scope = 'structural'
              AND confidence = 'edc-flagged'
              AND cluster_id IN (
                  SELECT DISTINCT cluster_id
                  FROM verdicts
                  WHERE method = 'consistency-check' AND result = 'flag'
              )
            """
        )
        conn.execute("DELETE FROM verdicts WHERE method = 'consistency-check' AND result = 'flag'")
        cluster_ids = [
            row[0]
            for row in conn.execute(
                """
                SELECT m.cluster_id
                FROM membership m
                JOIN clusters c ON c.cluster_id = m.cluster_id
                WHERE c.type = 'structural'
                GROUP BY m.cluster_id
                HAVING COUNT(*) > 1
                ORDER BY m.cluster_id
                """
            )
        ]
        flagged_clusters: list[tuple[str, str]] = []
        today = datetime.now(UTC).date().isoformat()
        for cluster_id in cluster_ids:
            rows = _cluster_rows(conn, cluster_id)
            reasons = _consistency_failures(rows)
            if not reasons:
                continue
            note = "; ".join(reasons)
            flagged_clusters.append((cluster_id, note))
            conn.execute(
                """
                UPDATE membership
                SET confidence = 'edc-flagged'
                WHERE cluster_id = ? AND scope = 'structural' AND confidence = 'edc'
                """,
                (cluster_id,),
            )
            conn.execute(
                """
                INSERT INTO verdicts(date, part, method, cluster_id, result, notes)
                VALUES (?, 'cluster', 'consistency-check', ?, 'flag', ?)
                """,
                (today, cluster_id, note),
            )
        conn.commit()
        structural_clusters = conn.execute(
            "SELECT COUNT(*) FROM clusters WHERE type = 'structural'"
        ).fetchone()[0]
        verdict_rows = conn.execute(
            "SELECT COUNT(*) FROM verdicts WHERE method = 'consistency-check' AND result = 'flag'"
        ).fetchone()[0]
    return {
        "structural_clusters": structural_clusters,
        "flagged_clusters": len(flagged_clusters),
        "duplicate_tuples": duplicate_count,
        "verdict_rows": verdict_rows,
    }
