"""Export clone_clusters.db to compact JSON (for HTML embed) and flat CSV."""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path


def to_json(db_path: str | Path) -> str:
    """Export vehicles, memberships, clusters as compact JSON string."""
    con = sqlite3.connect(str(db_path))
    c = con.cursor()
    v = [list(r) for r in c.execute(
        "SELECT id,make,model,styles,wheelbase,construction,drive,year_start,year_end "
        "FROM vehicles"
    )]
    m = [list(r) for r in c.execute(
        "SELECT vehicle_id,cluster_id,scope,confidence,IFNULL(note,'') FROM membership"
    )]
    clusters = {
        r[0]: [r[1], r[2] or ""]
        for r in c.execute("SELECT cluster_id,type,label FROM clusters")
    }
    con.close()
    return json.dumps({"v": v, "m": m, "c": clusters}, separators=(",", ":"))


def to_csv(db_path: str | Path, csv_path: str | Path) -> int:
    """Export flat joined view to CSV. Returns row count."""
    con = sqlite3.connect(str(db_path))
    c = con.cursor()
    rows = c.execute(
        """SELECT v.make, v.model, v.styles, v.wheelbase, v.construction, v.drive,
                  v.year_start, v.year_end, v.alt_models, v.remarks,
                  m.cluster_id, m.scope, m.confidence, IFNULL(m.note,'')
           FROM vehicles v
           LEFT JOIN membership m ON m.vehicle_id = v.id
           ORDER BY v.make, v.model, v.year_start"""
    ).fetchall()
    con.close()

    headers = [
        "make", "model", "styles", "wheelbase", "construction", "drive",
        "year_start", "year_end", "alt_models", "remarks",
        "cluster_id", "scope", "confidence", "note",
    ]
    with open(str(csv_path), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
    return len(rows)
