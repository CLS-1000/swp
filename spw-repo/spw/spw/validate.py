"""Gate 4: Deterministic consistency validation of structural clusters."""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path


def validate(db_path: str | Path) -> dict:
    """Run consistency checks on structural clusters.

    For each multi-member structural cluster, checks:
    - Construction type agreement (f vs u)
    - Shared drive-wheel intersection
    - Wheelbase spread ≤ 1.0"
    - Production-year overlap

    Failures → verdict row + confidence downgrade to 'edc-flagged'.
    Returns stats dict: total, flagged, details.
    """
    con = sqlite3.connect(str(db_path))
    c = con.cursor()
    today = datetime.date.today().isoformat()

    # clear previous consistency-check verdicts to allow re-run
    c.execute("DELETE FROM verdicts WHERE method='consistency-check'")
    # reset any previous flags back to edc
    c.execute(
        "UPDATE membership SET confidence='edc' "
        "WHERE confidence='edc-flagged' AND scope='structural'"
    )

    clusters = [
        r[0] for r in c.execute(
            "SELECT cluster_id FROM clusters WHERE type='structural'"
        ).fetchall()
    ]

    flagged = 0
    details: list[dict] = []

    for cid in clusters:
        rows = c.execute(
            """SELECT v.id, v.construction, v.drive, v.wheelbase,
                      v.year_start, v.year_end
               FROM vehicles v
               JOIN membership m ON m.vehicle_id = v.id
               WHERE m.cluster_id = ? AND m.scope = 'structural'""",
            (cid,),
        ).fetchall()
        if len(rows) < 2:
            continue

        problems: list[str] = []

        # construction agreement
        cons = {r[1] for r in rows if r[1]}
        if len(cons) > 1:
            problems.append(f"construction mismatch {cons}")

        # shared drive wheels
        drv_sets = []
        for r in rows:
            if r[2]:
                drv_sets.append(set(r[2].replace("/", ",").split(",")) - {""})
        if drv_sets and not set.intersection(*drv_sets):
            problems.append("no shared drive wheels")

        # wheelbase spread
        wbs: list[float] = []
        for r in rows:
            if r[3]:
                try:
                    wbs.append(float(r[3].split(",")[0]))
                except ValueError:
                    pass
        if wbs and max(wbs) - min(wbs) > 1.0:
            problems.append(f"wheelbase spread {max(wbs) - min(wbs):.1f}in")

        # production-year overlap
        ys = max(r[4] for r in rows)
        ye = min(r[5] for r in rows)
        if ys > ye:
            problems.append("no production-year overlap")

        if problems:
            flagged += 1
            note = "; ".join(problems)
            c.execute(
                "INSERT INTO verdicts(date,part,method,cluster_id,result,notes) "
                "VALUES(?,?,?,?,?,?)",
                (today, "(structural)", "consistency-check", cid, "flag", note),
            )
            c.execute(
                "UPDATE membership SET confidence='edc-flagged' "
                "WHERE cluster_id=? AND scope='structural'",
                (cid,),
            )
            details.append({"cluster_id": cid, "problems": note})

    con.commit()
    con.close()

    return {"total": len(clusters), "flagged": flagged, "details": details}
