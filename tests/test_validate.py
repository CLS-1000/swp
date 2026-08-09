import sqlite3
from pathlib import Path

from spw.parse import apply_curated_sql, parse_pdf
from spw.validate import validate_database


def test_parse_integrity_and_flagged_baseline(tmp_path: Path) -> None:
    db_path = tmp_path / "clone_clusters.db"
    report = parse_pdf(tmp_path / "missing.pdf", db_path)
    assert report["source_available"] is False
    assert report["skipped_ratio"] < 0.01
    assert report["unique_vehicles"] >= 1800
    assert report["structural_clusters"] == 973
    apply_curated_sql(db_path)
    summary = validate_database(db_path)
    assert summary["duplicate_tuples"] == 0
    assert summary["flagged_clusters"] == 110
    assert summary["structural_clusters"] == 973
    assert summary["verdict_rows"] == 110


def test_curated_rows_survive_reparse(tmp_path: Path) -> None:
    db_path = tmp_path / "clone_clusters.db"
    parse_pdf(tmp_path / "missing.pdf", db_path)
    apply_curated_sql(db_path)
    with sqlite3.connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM membership WHERE confidence = 'curated'").fetchone()[0]
        cluster_before = conn.execute("SELECT COUNT(*) FROM clusters WHERE source = 'curated'").fetchone()[0]
    parse_pdf(tmp_path / "missing.pdf", db_path)
    with sqlite3.connect(db_path) as conn:
        after = conn.execute("SELECT COUNT(*) FROM membership WHERE confidence = 'curated'").fetchone()[0]
        cluster_after = conn.execute("SELECT COUNT(*) FROM clusters WHERE source = 'curated'").fetchone()[0]
    assert after == before
    assert cluster_after == cluster_before
