"""Donor pool lookup — resolve a vehicle to its structural + engine-family siblings."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Donor:
    cluster_type: str
    scope: str
    confidence: str
    make: str
    model: str
    year_start: int
    year_end: int
    note: str


def _match_model_sql(user_model: str) -> tuple[str, list]:
    """Build SQL WHERE fragments for model matching.

    Rules (must match frontend logic):
    1. Case-insensitive substring either direction.
    2. 3-digit badge from user model matches any comma/slash token in row.
    3. Badge leading digit N matches row = 'N-Series'.
    """
    um = user_model.strip().lower()
    badge_m = re.search(r"(\d{3})", um)
    badge = badge_m.group(1) if badge_m else None
    series = badge[0] if badge else (re.match(r"^(\d)", um).group(1) if re.match(r"^(\d)", um) else None)

    # We'll do matching in Python since SQL LIKE can't express all rules cleanly.
    # Return a broad filter and refine in Python.
    return um, badge, series


def donor_pool(
    make: str, model: str, year: int, db_path: str | Path = "clone_clusters.db"
) -> list[Donor]:
    """Return donor vehicles grouped by cluster type with confidence tags.

    Matches subject vehicle to database rows per model-matching rules,
    then returns all cluster peers excluding the subject itself.
    """
    con = sqlite3.connect(str(db_path))
    c = con.cursor()

    um, badge, series = _match_model_sql(model)

    # fetch all candidate vehicles for this make/year
    candidates = c.execute(
        """SELECT id, model FROM vehicles
           WHERE LOWER(make) = ? AND year_start <= ? AND year_end >= ?""",
        (make.strip().lower(), year, year),
    ).fetchall()

    # apply model matching rules
    subject_ids: set[int] = set()
    for vid, row_model in candidates:
        rm = row_model.lower()
        # rule 1: substring either direction
        if um in rm or rm in um:
            subject_ids.add(vid)
            continue
        # rule 2: badge match against tokens
        if badge:
            tokens = re.split(r"[,/\s]+", rm)
            if any(t.startswith(badge) for t in tokens):
                subject_ids.add(vid)
                continue
        # rule 3: N-Series
        if series and rm == f"{series}-series":
            subject_ids.add(vid)

    if not subject_ids:
        con.close()
        return []

    # find all cluster peers
    placeholders = ",".join("?" * len(subject_ids))
    rows = c.execute(
        f"""SELECT DISTINCT c.type, m2.scope, m2.confidence,
                   v2.make, v2.model, v2.year_start, v2.year_end,
                   IFNULL(m2.note, '')
            FROM membership m1
            JOIN clusters c ON c.cluster_id = m1.cluster_id
            JOIN membership m2 ON m2.cluster_id = m1.cluster_id
            JOIN vehicles v2 ON v2.id = m2.vehicle_id
            WHERE m1.vehicle_id IN ({placeholders})
              AND m2.vehicle_id NOT IN ({placeholders})
            ORDER BY c.type, v2.make, v2.model""",
        list(subject_ids) + list(subject_ids),
    ).fetchall()

    con.close()

    # dedupe on (make, model, note, cluster_type)
    seen: set[tuple] = set()
    donors: list[Donor] = []
    for r in rows:
        key = (r[0], r[3], r[4], r[7])
        if key in seen:
            continue
        seen.add(key)
        donors.append(Donor(
            cluster_type=r[0], scope=r[1], confidence=r[2],
            make=r[3], model=r[4], year_start=r[5], year_end=r[6], note=r[7],
        ))
    return donors


def format_pool(donors: list[Donor]) -> str:
    """Format donor pool as human-readable text for CLI output."""
    if not donors:
        return "No donor pool matches found. EDC coverage ends 2012."

    structural = [d for d in donors if d.cluster_type == "structural"]
    engine = [d for d in donors if d.cluster_type == "engine_family"]

    lines: list[str] = []

    def _tag(conf: str) -> str:
        if conf == "edc":
            return "[EDC]"
        elif conf == "edc-flagged":
            return "[EDC ⚠ FLAGGED]"
        return "[CURATED]"

    if structural:
        lines.append("STRUCTURAL SIBLINGS (body / chassis / suspension)")
        lines.append(f"  {'Make':<12} {'Model':<22} {'Years':<12} {'Confidence':<18} Qualifier")
        lines.append("  " + "-" * 80)
        for d in structural:
            lines.append(
                f"  {d.make:<12} {d.model:<22} {d.year_start}–{d.year_end:<7} "
                f"{_tag(d.confidence):<18} {d.note or '—'}"
            )

    if engine:
        if lines:
            lines.append("")
        lines.append("ENGINE FAMILY (engine-loop accessories)")
        lines.append(f"  {'Make':<12} {'Model':<22} {'Years':<12} {'Confidence':<18} Qualifier")
        lines.append("  " + "-" * 80)
        for d in engine:
            lines.append(
                f"  {d.make:<12} {d.model:<22} {d.year_start}–{d.year_end:<7} "
                f"{_tag(d.confidence):<18} {d.note or '—'}"
            )

    return "\n".join(lines)
