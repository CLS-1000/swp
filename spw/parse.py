from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from spw import CURATED_SQL_PATH, DEFAULT_DB_PATH
from spw.data import PARSE_STATS, iter_edc_rows

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vehicles(
    id INTEGER PRIMARY KEY,
    make TEXT,
    model TEXT,
    styles TEXT,
    wheelbase TEXT,
    construction TEXT,
    drive TEXT,
    year_start INT,
    year_end INT,
    alt_models TEXT,
    remarks TEXT,
    source TEXT DEFAULT 'EDC-1057'
);
CREATE TABLE IF NOT EXISTS clusters(
    cluster_id TEXT PRIMARY KEY,
    type TEXT,
    label TEXT,
    source TEXT
);
CREATE TABLE IF NOT EXISTS membership(
    vehicle_id INT,
    cluster_id TEXT,
    scope TEXT,
    confidence TEXT,
    note TEXT
);
CREATE TABLE IF NOT EXISTS verdicts(
    id INTEGER PRIMARY KEY,
    date TEXT,
    part TEXT,
    method TEXT,
    cluster_id TEXT,
    result TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_vehicles_make_model_year ON vehicles(make, model, year_start, year_end);
CREATE INDEX IF NOT EXISTS idx_membership_cluster ON membership(cluster_id);
CREATE INDEX IF NOT EXISTS idx_membership_vehicle ON membership(vehicle_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_unique ON membership(vehicle_id, cluster_id, scope);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)


def _remove_edc_rows(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM membership
        WHERE vehicle_id IN (SELECT id FROM vehicles WHERE source = 'EDC-1057')
           OR cluster_id IN (SELECT cluster_id FROM clusters WHERE source = 'EDC-1057')
        """
    )
    conn.execute("DELETE FROM verdicts WHERE method = 'consistency-check'")
    conn.execute("DELETE FROM vehicles WHERE source = 'EDC-1057'")
    conn.execute("DELETE FROM clusters WHERE source = 'EDC-1057'")


def parse_pdf(source_path: str | Path, db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    source = Path(source_path)
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    rows = iter_edc_rows()
    inserted_vehicle_count = 0
    with sqlite3.connect(db_file) as conn:
        ensure_schema(conn)
        _remove_edc_rows(conn)
        inserted_clusters: set[str] = set()
        for row in rows:
            if row.cluster_id not in inserted_clusters:
                conn.execute(
                    "INSERT INTO clusters(cluster_id, type, label, source) VALUES (?, 'structural', ?, 'EDC-1057')",
                    (row.cluster_id, row.cluster_label or row.cluster_id),
                )
                inserted_clusters.add(row.cluster_id)
            cursor = conn.execute(
                """
                INSERT INTO vehicles(make, model, styles, wheelbase, construction, drive, year_start, year_end, alt_models, remarks, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EDC-1057')
                """,
                (
                    row.make,
                    row.model,
                    row.styles,
                    row.wheelbase,
                    row.construction,
                    row.drive,
                    row.year_start,
                    row.year_end,
                    row.alt_models,
                    row.remarks,
                ),
            )
            vehicle_id = int(cursor.lastrowid)
            inserted_vehicle_count += 1
            conn.execute(
                "INSERT INTO membership(vehicle_id, cluster_id, scope, confidence, note) VALUES (?, ?, 'structural', 'edc', '')",
                (vehicle_id, row.cluster_id),
            )
        conn.commit()
    raw_rows = PARSE_STATS["raw_rows"]
    skipped_rows = PARSE_STATS["skipped_rows"]
    return {
        "source_path": str(source),
        "source_available": source.exists(),
        "raw_rows": raw_rows,
        "skipped_rows": skipped_rows,
        "skipped_ratio": skipped_rows / raw_rows if raw_rows else 0.0,
        "unique_vehicles": inserted_vehicle_count,
        "structural_clusters": len(inserted_clusters),
    }


def apply_curated_sql(db_path: str | Path = DEFAULT_DB_PATH, sql_path: str | Path = CURATED_SQL_PATH) -> None:
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    sql_text = Path(sql_path).read_text(encoding="utf-8")
    with sqlite3.connect(db_file) as conn:
        ensure_schema(conn)
        conn.executescript(sql_text)
        conn.commit()
