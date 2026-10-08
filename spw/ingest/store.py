from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from spw import KB_PATH
from spw.ingest.chunk import chunk_text
from spw.ingest.readers import ReadError, SpecRow, read_pdf, read_spec_table, read_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, kind TEXT, pages INT, chunks INT, spec_rows INT, ingested_at TEXT
);
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY, file TEXT NOT NULL, page INT, section TEXT, sha256 TEXT, ord INT, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, section, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS specs(
    id INTEGER PRIMARY KEY, make TEXT, model TEXT, year_start INT, year_end INT, ecu_family TEXT,
    quantity TEXT, spec_key TEXT, value REAL, vmin REAL, vmax REAL, unit TEXT, measurement_state TEXT,
    source_file TEXT NOT NULL, page INT
);
CREATE INDEX IF NOT EXISTS idx_specs_key ON specs(spec_key);
"""

TEXT_SUFFIXES = {".md", ".txt"}
TABLE_SUFFIXES = {".csv", ".xlsx"}


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def spec_key(ecu_family: str, quantity: str, state: str) -> str:
    """marelli + ECU supply + KOEO -> 'marelli.ecu_supply_koeo'."""
    tail = slug(quantity) + (f"_{slug(state)}" if state else "")
    return f"{slug(ecu_family)}.{tail}" if ecu_family else tail


@dataclass(frozen=True)
class Chunk:
    file: str
    page: int
    section: str
    text: str
    score: float = 0.0

    @property
    def cite(self) -> str:
        return f"{self.file} p.{self.page}"


@dataclass
class IngestReport:
    files_ingested: int = 0
    pages: int = 0
    chunks: int = 0
    spec_rows: int = 0
    skipped: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "files_ingested": self.files_ingested,
            "pages": self.pages,
            "chunks": self.chunks,
            "spec_rows": self.spec_rows,
            "skipped": self.skipped,
        }


def connect(kb_path: str | Path = KB_PATH) -> sqlite3.Connection:
    path = Path(kb_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _drop_file(conn: sqlite3.Connection, rel: str) -> None:
    ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE file=?", (rel,))]
    conn.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
    conn.execute("DELETE FROM chunks WHERE file=?", (rel,))
    conn.execute("DELETE FROM specs WHERE source_file=?", (rel,))
    conn.execute("DELETE FROM files WHERE path=?", (rel,))


def _add_chunks(conn: sqlite3.Connection, rel: str, sha: str, sections) -> tuple[int, int]:
    pages = {s.page for s in sections}
    n = 0
    for sec in sections:
        for ordinal, piece in enumerate(chunk_text(sec.text)):
            cur = conn.execute(
                "INSERT INTO chunks(file,page,section,sha256,ord,text) VALUES(?,?,?,?,?,?)",
                (rel, sec.page, sec.section, sha, ordinal, piece),
            )
            conn.execute("INSERT INTO chunks_fts(rowid,text,section) VALUES(?,?,?)", (cur.lastrowid, piece, sec.section))
            n += 1
    return len(pages), n


def _add_specs(conn: sqlite3.Connection, rel: str, rows: list[SpecRow]) -> int:
    for r in rows:
        conn.execute(
            "INSERT INTO specs(make,model,year_start,year_end,ecu_family,quantity,spec_key,value,vmin,vmax,unit,"
            "measurement_state,source_file,page) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r.make, r.model, r.year_start, r.year_end, r.ecu_family, r.quantity,
                spec_key(r.ecu_family, r.quantity, r.measurement_state),
                r.value, r.vmin, r.vmax, r.unit, r.measurement_state, rel, r.page,
            ),
        )
    return len(rows)


def ingest_dir(source: str | Path, kb_path: str | Path = KB_PATH) -> IngestReport:
    root = Path(source)
    report = IngestReport()
    if not root.is_dir():
        report.skipped.append({"file": str(root), "reason": "not a directory"})
        return report
    conn = connect(kb_path)
    try:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root).as_posix()
            suffix = path.suffix.lower()
            if suffix not in TEXT_SUFFIXES | TABLE_SUFFIXES | {".pdf"}:
                report.skipped.append({"file": rel, "reason": f"unsupported type {suffix or '(none)'}"})
                continue
            sha = _sha256(path)
            known = conn.execute("SELECT sha256 FROM files WHERE path=?", (rel,)).fetchone()
            if known and known["sha256"] == sha:
                report.skipped.append({"file": rel, "reason": "unchanged"})
                continue
            try:
                if suffix == ".pdf":
                    sections, specs, kind = read_pdf(path), [], "pdf"
                elif suffix in TEXT_SUFFIXES:
                    sections, specs, kind = read_text(path), [], "text"
                else:
                    specs, _ = read_spec_table(path)
                    sections, kind = [], "table"
            except ReadError as exc:
                report.skipped.append({"file": rel, "reason": str(exc)})
                continue
            if not any(s.text.strip() for s in sections) and not specs:
                report.skipped.append({"file": rel, "reason": "no extractable text"})
                continue
            with conn:
                _drop_file(conn, rel)
                pages, nchunks = _add_chunks(conn, rel, sha, sections)
                nspecs = _add_specs(conn, rel, specs)
                conn.execute(
                    "INSERT INTO files VALUES(?,?,?,?,?,?,datetime('now'))", (rel, sha, kind, pages, nchunks, nspecs)
                )
            report.files_ingested += 1
            report.pages += pages
            report.chunks += nchunks
            report.spec_rows += nspecs
    finally:
        conn.close()
    return report


def stats(kb_path: str | Path = KB_PATH) -> dict:
    conn = connect(kb_path)
    try:
        return {
            "files": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "spec_rows": conn.execute("SELECT COUNT(*) FROM specs").fetchone()[0],
        }
    finally:
        conn.close()


_WORD = re.compile(r"[A-Za-z0-9]{2,}")


def retrieve(query: str, k: int = 4, kb_path: str | Path = KB_PATH) -> list[Chunk]:
    """BM25 over chunks. This is the single seam to swap for embeddings later."""
    terms = list(dict.fromkeys(w.lower() for w in _WORD.findall(query)))
    if not terms or not Path(kb_path).exists():
        return []
    match = " OR ".join(f'"{t}"' for t in terms)
    conn = connect(kb_path)
    try:
        rows = conn.execute(
            "SELECT c.file,c.page,c.section,c.text,bm25(chunks_fts) AS score FROM chunks_fts "
            "JOIN chunks c ON c.id=chunks_fts.rowid WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
            (match, k),
        ).fetchall()
    finally:
        conn.close()
    return [Chunk(r["file"], r["page"], r["section"] or "", r["text"], r["score"]) for r in rows]
