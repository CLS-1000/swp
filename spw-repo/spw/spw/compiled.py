"""Compiled overlay: modern vehicles, platform clusters and engine families.

The EDC-1057 source stops at model year 2013 and carries no engine data at all,
so on its own the donor pool is empty for anything built in the last decade.
This module loads a third data tier on top of it:

* ``compiled_clusters.csv`` — platform and engine-family cluster definitions
* ``compiled_vehicles.csv``  — 2013-2025 vehicles with their platform/engine tags
* ``compiled_legacy.csv``    — rules that attach engine families to EDC vehicles

Everything loaded here is written with ``confidence='compiled'`` and
``source='compiled'``. That tier is deliberately *below* ``curated``: these rows
are compiled from published engine-application and platform-sharing data, not
hand-verified against a crash-reconstruction authority. Treat them as a starting
point for a donor search, not as a purchase decision.

Re-running is idempotent — every compiled row is deleted before reload, so the
EDC and curated tiers are never touched.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

# Compiled vehicles occupy a reserved id range so they can never collide with
# the autoincrement ids the EDC parser hands out.
BASE_VEHICLE_ID = 900_001

CLUSTERS_CSV = "compiled_clusters.csv"
VEHICLES_CSV = "compiled_vehicles.csv"
LEGACY_CSV = "compiled_legacy.csv"


def _split_tags(field: str) -> list[tuple[str, str]]:
    """Parse a ``CID:note;CID:note`` tag field into (cluster_id, note) pairs."""
    tags: list[tuple[str, str]] = []
    for chunk in (field or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        cid, _, note = chunk.partition(":")
        tags.append((cid.strip(), note.strip()))
    return tags


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def apply_compiled(db_path: str | Path, assets_dir: str | Path) -> dict:
    """Load the compiled overlay into an existing database.

    Returns a stats dict with keys: clusters, vehicles, memberships,
    legacy_links, unknown_clusters, empty_rules.
    """
    db_path, assets_dir = Path(db_path), Path(assets_dir)
    clusters_path = assets_dir / CLUSTERS_CSV
    vehicles_path = assets_dir / VEHICLES_CSV
    legacy_path = assets_dir / LEGACY_CSV
    if not clusters_path.exists():
        return {
            "clusters": 0, "vehicles": 0, "memberships": 0, "legacy_links": 0,
            "unknown_clusters": [], "empty_rules": [],
        }

    con = sqlite3.connect(str(db_path))
    c = con.cursor()

    # wipe the previous compiled tier — EDC and curated rows are left alone
    c.execute("DELETE FROM membership WHERE confidence='compiled'")
    c.execute("DELETE FROM clusters WHERE source='compiled'")
    c.execute("DELETE FROM vehicles WHERE source='compiled'")

    known: set[str] = set()
    for row in _read_csv(clusters_path):
        c.execute(
            "INSERT OR REPLACE INTO clusters VALUES(?,?,?,?)",
            (row["cluster_id"], row["type"], row["label"], "compiled"),
        )
        known.add(row["cluster_id"])
    n_clusters = len(known)

    unknown: list[str] = []
    n_vehicles = n_members = 0

    for i, row in enumerate(_read_csv(vehicles_path)):
        vid = BASE_VEHICLE_ID + i
        c.execute(
            """INSERT INTO vehicles(id,make,model,styles,wheelbase,construction,drive,
                                    year_start,year_end,alt_models,remarks,source)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid, row["make"], row["model"], row["styles"], row["wheelbase"],
             row["construction"], row["drive"], int(row["year_start"]),
             int(row["year_end"]), "", row["remarks"], "compiled"),
        )
        n_vehicles += 1

        tagged = [("platform", t) for t in _split_tags(row["platform"])]
        tagged += [("engine-loop", t) for t in _split_tags(row["engines"])]
        for scope, (cid, note) in tagged:
            if cid not in known:
                unknown.append(f"{row['make']} {row['model']} -> {cid}")
                continue
            c.execute(
                "INSERT INTO membership(vehicle_id,cluster_id,scope,confidence,note) "
                "VALUES(?,?,?,?,?)",
                (vid, cid, scope, "compiled", note),
            )
            n_members += 1

    # legacy rules: attach engine families to vehicles the EDC parser produced
    n_legacy = 0
    empty_rules: list[str] = []
    for row in _read_csv(legacy_path):
        cid = row["cluster_id"]
        rule = f"{cid} <- {row['make']} {row['model_like']}"
        if cid not in known:
            unknown.append(rule)
            continue
        cur = c.execute(
            """INSERT INTO membership(vehicle_id,cluster_id,scope,confidence,note)
               SELECT id,?,?,?,? FROM vehicles
               WHERE make=? AND model LIKE ? AND source<>'compiled'
                 AND year_start<=? AND year_end>=?""",
            (cid, "engine-loop", "compiled", row["note"], row["make"],
             row["model_like"], int(row["year_to"]), int(row["year_from"])),
        )
        if cur.rowcount:
            n_legacy += cur.rowcount
        else:
            empty_rules.append(rule)

    con.commit()
    con.close()

    return {
        "clusters": n_clusters,
        "vehicles": n_vehicles,
        "memberships": n_members,
        "legacy_links": n_legacy,
        "unknown_clusters": unknown,
        "empty_rules": empty_rules,
    }
