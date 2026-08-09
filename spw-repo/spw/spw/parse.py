"""Gate 1: Parse EDC-1057 PDF into normalized SQLite clone-cluster dataset."""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
from pathlib import Path

SCHEMA = """\
CREATE TABLE IF NOT EXISTS vehicles(
  id INTEGER PRIMARY KEY, make TEXT, model TEXT, styles TEXT,
  wheelbase TEXT, construction TEXT, drive TEXT, year_start INT, year_end INT,
  alt_models TEXT, remarks TEXT, source TEXT DEFAULT 'EDC-1057');
CREATE TABLE IF NOT EXISTS clusters(
  cluster_id TEXT PRIMARY KEY, type TEXT, label TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS membership(
  vehicle_id INT, cluster_id TEXT, scope TEXT, confidence TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS verdicts(
  id INTEGER PRIMARY KEY, date TEXT, part TEXT, method TEXT,
  cluster_id TEXT, result TEXT, notes TEXT);
CREATE INDEX IF NOT EXISTS idx_v ON vehicles(make, model);
CREATE INDEX IF NOT EXISTS idx_m ON membership(cluster_id);
"""

ROW_RE = re.compile(
    r"^(?P<head>.*?)\s+"
    r"(?P<body>[fu](?:/[fu])?)\s+"
    r"(?P<drv>,?[fr4a](?:\s*[,/]\s*[fr4a])*)\s+"
    r"(?P<y0>\d{4})\s+(?P<y1>\d{4})(?P<tail>.*)$"
)
WB_RE = re.compile(r"^[\d.]+(,[\d.]+)*$")
SEC_RE = re.compile(r"^\s+\d{4}\s+All Cars")
PG_RE = re.compile(r"^\s*Page \d+\s*$")
MAKE_MAX_INDENT = 10


def _pdf_to_text(pdf_path: Path) -> str:
    """Convert PDF to text using pdftotext with layout preservation."""
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _extract_rows(text: str) -> tuple[list[dict], list[str]]:
    """Parse raw text into row dicts. Returns (rows, skipped_lines)."""
    rows: list[dict] = []
    skipped: list[str] = []
    rem_off: int | None = None

    for page in text.split("\f"):
        for line in page.splitlines():
            if not line.strip() or PG_RE.match(line) or SEC_RE.match(line):
                continue
            if "MAKE" in line and "MODEL" in line and "STYLES" in line:
                rem_off = line.find("REMARKS")
                continue
            m = ROW_RE.match(line)
            if not m:
                if len(line.strip()) > 25 and re.search(r"\d{4}\s+\d{4}", line):
                    skipped.append(line.rstrip())
                continue

            head = m.group("head")
            indent = len(line) - len(line.lstrip())
            toks = re.split(r"\s{2,}", head.strip())
            make = ""
            if indent <= MAKE_MAX_INDENT and len(toks) >= 2:
                make = toks[0]
                toks = toks[1:]
            model = toks[0] if toks else ""
            wb, styles = "", ""
            if len(toks) >= 2:
                if WB_RE.match(toks[-1]):
                    wb = toks[-1]
                    styles = " ".join(toks[1:-1])
                else:
                    styles = " ".join(toks[1:])

            tail = m.group("tail")
            cm = re.search(r"(\d{6})\s*$", tail)
            clone = cm.group(1) if cm else None
            tb = tail[: cm.start()] if cm else tail
            abs_start = m.start("tail")
            alt = remarks = ""
            if rem_off and rem_off > abs_start and rem_off - abs_start < len(tb):
                cut = rem_off - abs_start
                alt, remarks = tb[:cut].strip(), tb[cut:].strip()
            else:
                alt = tb.strip()

            rows.append(
                dict(
                    make=make, model=model, styles=styles, wb=wb,
                    body=m.group("body"), drive=re.sub(r"\s+", "", m.group("drv")),
                    y0=int(m.group("y0")), y1=int(m.group("y1")),
                    alt=alt, remarks=remarks, clone=clone,
                )
            )

    # carry forward make on continuation rows
    cur = None
    for r in rows:
        if r["make"]:
            cur = r["make"]
        else:
            r["make"] = cur

    return rows, skipped


def _dedupe(rows: list[dict]) -> list[dict]:
    """Remove duplicate rows by (make, model, styles, year_start, year_end, clone)."""
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for r in rows:
        k = (r["make"], r["model"], r["styles"], r["y0"], r["y1"], r["clone"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def parse(pdf_path: str | Path, db_path: str | Path) -> dict:
    """Parse EDC-1057 PDF into SQLite database.

    Returns stats dict with keys: raw, unique, clusters, skipped, skipped_ratio.
    """
    pdf_path = Path(pdf_path)
    db_path = Path(db_path)

    text = _pdf_to_text(pdf_path)
    rows, skipped = _extract_rows(text)
    uniq = _dedupe(rows)

    con = sqlite3.connect(str(db_path))
    c = con.cursor()

    c.executescript(SCHEMA)
    # clear only EDC-sourced data; curated rows are handled separately
    c.execute("DELETE FROM membership WHERE confidence IN ('edc','edc-flagged')")
    c.execute("DELETE FROM clusters WHERE source='EDC-1057'")
    c.execute("DELETE FROM vehicles WHERE source='EDC-1057'")

    for r in uniq:
        c.execute(
            """INSERT INTO vehicles(make,model,styles,wheelbase,construction,drive,
               year_start,year_end,alt_models,remarks) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (r["make"], r["model"], r["styles"], r["wb"], r["body"], r["drive"],
             r["y0"], r["y1"], r["alt"], r["remarks"]),
        )
        vid = c.lastrowid
        if r["clone"]:
            c.execute(
                "INSERT OR IGNORE INTO clusters VALUES(?,?,?,?)",
                (r["clone"], "structural", None, "EDC-1057"),
            )
            c.execute(
                "INSERT INTO membership(vehicle_id,cluster_id,scope,confidence) VALUES(?,?,?,?)",
                (vid, r["clone"], "structural", "edc"),
            )

    con.commit()

    n_cl = c.execute(
        "SELECT COUNT(*) FROM clusters WHERE type='structural'"
    ).fetchone()[0]

    stats = {
        "raw": len(rows),
        "unique": len(uniq),
        "clusters": n_cl,
        "skipped": len(skipped),
        "skipped_ratio": len(skipped) / max(len(rows), 1),
    }
    con.close()
    return stats


def apply_curated(db_path: str | Path, sql_path: str | Path) -> int:
    """Apply curated.sql on top of parsed database. Returns rows affected."""
    db_path, sql_path = Path(db_path), Path(sql_path)
    if not sql_path.exists():
        return 0
    con = sqlite3.connect(str(db_path))
    # clear previous curated memberships to allow re-application
    con.execute("DELETE FROM membership WHERE confidence='curated'")
    con.execute("DELETE FROM clusters WHERE source='curated'")
    sql = sql_path.read_text()
    con.executescript(sql)
    n = con.execute("SELECT COUNT(*) FROM membership WHERE confidence='curated'").fetchone()[0]
    con.commit()
    con.close()
    return n
