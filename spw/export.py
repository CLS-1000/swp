from __future__ import annotations

import csv
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spw import DEFAULT_DB_PATH, DIST_DIR
from spw.parse import ensure_schema

EXPORT_HEADERS = [
    "make",
    "model",
    "year_start",
    "year_end",
    "cluster_id",
    "type",
    "confidence",
    "note",
    "styles",
]


def export_dataset(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    with sqlite3.connect(Path(db_path)) as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT v.make, v.model, v.year_start, v.year_end, m.cluster_id, c.type, m.confidence, COALESCE(m.note, ''), COALESCE(v.styles, '')
            FROM membership m
            JOIN vehicles v ON v.id = m.vehicle_id
            JOIN clusters c ON c.cluster_id = m.cluster_id
            ORDER BY c.type, m.cluster_id, lower(v.make), lower(v.model), v.year_start
            """
        ).fetchall()
    payload_rows = [list(row) for row in rows]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "rows": payload_rows,
        "row_count": len(payload_rows),
    }


def write_exports(db_path: str | Path = DEFAULT_DB_PATH, dist_dir: str | Path = DIST_DIR) -> dict[str, Path]:
    payload = export_dataset(db_path)
    destination = Path(dist_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "spw.json"
    csv_path = destination / "spw.csv"
    json_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(EXPORT_HEADERS)
        writer.writerows(payload["rows"])
    return {"json": json_path, "csv": csv_path}
