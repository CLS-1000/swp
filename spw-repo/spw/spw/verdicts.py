"""Verdict feedback log — CRUD for calibration data."""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path


def add_verdict(
    db_path: str | Path,
    part: str,
    method: str,
    cluster_id: str,
    result: str,
    notes: str = "",
) -> int:
    """Insert a verdict row. Returns the new row id."""
    con = sqlite3.connect(str(db_path))
    c = con.cursor()
    c.execute(
        "INSERT INTO verdicts(date,part,method,cluster_id,result,notes) "
        "VALUES(?,?,?,?,?,?)",
        (datetime.date.today().isoformat(), part, method, cluster_id, result, notes),
    )
    rid = c.lastrowid
    con.commit()
    con.close()
    return rid


def report(db_path: str | Path) -> str:
    """Generate pass/fail rates by method and cluster."""
    con = sqlite3.connect(str(db_path))
    c = con.cursor()

    rows = c.execute(
        """SELECT method, cluster_id, result, COUNT(*) n
           FROM verdicts
           GROUP BY method, cluster_id, result
           ORDER BY method, cluster_id, result"""
    ).fetchall()
    con.close()

    if not rows:
        return "No verdicts logged."

    lines = ["VERDICT REPORT", f"  {'Method':<22} {'Cluster':<16} {'Result':<10} Count"]
    lines.append("  " + "-" * 60)
    for method, cid, result, n in rows:
        lines.append(f"  {method:<22} {cid:<16} {result:<10} {n}")

    return "\n".join(lines)
