from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from spw import DEFAULT_DB_PATH
from spw.parse import ensure_schema

BADGE_RE = re.compile(r"(\d{3})")
LOOKUP_RE = re.compile(r"^(?P<year>\d{4})\s+(?P<make>\S+)\s+(?P<model>.+)$")


def model_matches(user_model: str, row_model: str) -> bool:
    left = user_model.strip().casefold()
    right = row_model.strip().casefold()
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    badge_match = BADGE_RE.search(left)
    if not badge_match:
        return False
    badge = badge_match.group(1)
    tokens = [token.strip().casefold() for token in re.split(r"[,/]", right) if token.strip()]
    if any(token.startswith(badge) for token in tokens):
        return True
    return right == f"{badge[0]}-series"


def parse_lookup_query(query: str) -> tuple[str, str, int]:
    match = LOOKUP_RE.match(query.strip())
    if not match:
        raise ValueError("lookup query must look like '2008 BMW 528i'")
    return match.group("make"), match.group("model"), int(match.group("year"))


def donor_pool(make: str, model: str, year: int, db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    db_file = Path(db_path)
    with sqlite3.connect(db_file) as conn:
        ensure_schema(conn)
        conn.row_factory = sqlite3.Row
        candidates = list(
            conn.execute(
                """
                SELECT *
                FROM vehicles
                WHERE lower(make) = lower(?) AND ? BETWEEN year_start AND year_end
                ORDER BY id
                """,
                (make, year),
            )
        )
        matched = [row for row in candidates if model_matches(model, row["model"])]
        matched_ids = [row["id"] for row in matched]
        cluster_ids = sorted({
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT cluster_id FROM membership WHERE vehicle_id IN ({','.join('?' for _ in matched_ids)})",
                tuple(matched_ids),
            )
        }) if matched_ids else []
        result: dict[str, list[dict[str, Any]]] = {"structural": [], "engine_family": []}
        if not cluster_ids:
            return {"input": {"make": make, "model": model, "year": year}, **result}
        placeholders = ",".join("?" for _ in cluster_ids)
        exclude_placeholders = ",".join("?" for _ in matched_ids)
        rows = list(
            conn.execute(
                f"""
                SELECT v.make, v.model, v.styles, v.year_start, v.year_end, m.cluster_id, m.confidence, m.note, c.type
                FROM membership m
                JOIN vehicles v ON v.id = m.vehicle_id
                JOIN clusters c ON c.cluster_id = m.cluster_id
                WHERE m.cluster_id IN ({placeholders})
                  AND v.id NOT IN ({exclude_placeholders})
                ORDER BY c.type, m.cluster_id, lower(v.make), lower(v.model), v.year_start
                """,
                tuple(cluster_ids) + tuple(matched_ids),
            )
        )
        seen: dict[str, set[tuple[str, str, str]]] = {"structural": set(), "engine_family": set()}
        for row in rows:
            key = (row["make"].casefold(), row["model"].casefold(), (row["note"] or "").casefold())
            bucket = "structural" if row["type"] == "structural" else "engine_family"
            if key in seen[bucket]:
                continue
            seen[bucket].add(key)
            result[bucket].append(
                {
                    "make": row["make"],
                    "model": row["model"],
                    "styles": row["styles"],
                    "year_start": row["year_start"],
                    "year_end": row["year_end"],
                    "cluster_id": row["cluster_id"],
                    "confidence": row["confidence"],
                    "note": row["note"] or "",
                }
            )
    return {"input": {"make": make, "model": model, "year": year}, **result}


def format_donor_pool(result: dict[str, Any]) -> str:
    lines = [
        f'Lookup: {result["input"]["year"]} {result["input"]["make"]} {result["input"]["model"]}',
        '',
    ]
    for title, key in (("Structural siblings", "structural"), ("Engine family", "engine_family")):
        lines.append(f"{title}:")
        rows = result[key]
        if not rows:
            lines.append("  (none)")
        for row in rows:
            note = f" — {row['note']}" if row["note"] else ""
            lines.append(
                f"  - {row['make']} {row['model']} {row['styles']} {row['year_start']}-{row['year_end']} "
                f"[{row['confidence'].upper()}]{note}"
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"
