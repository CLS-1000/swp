from pathlib import Path

from spw.lookup import donor_pool
from spw.parse import apply_curated_sql, parse_pdf
from spw.validate import validate_database


def build_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "clone_clusters.db"
    parse_pdf(tmp_path / "missing.pdf", db_path)
    apply_curated_sql(db_path)
    validate_database(db_path)
    return db_path


def test_tahoe_structural_pool_contains_escalade_and_yukon(tmp_path: Path) -> None:
    db_path = build_db(tmp_path)
    result = donor_pool("Chevrolet", "Tahoe", 2010, db_path)
    models = {(row["make"], row["model"]) for row in result["structural"]}
    assert ("Cadillac", "Escalade") in models
    assert ("GMC", "Yukon") in models


def test_frs_and_brz_share_cluster_007710(tmp_path: Path) -> None:
    db_path = build_db(tmp_path)
    frs = donor_pool("Scion", "FR-S", 2013, db_path)
    brz = donor_pool("Subaru", "BRZ", 2013, db_path)
    assert any(row["make"] == "Subaru" and row["model"] == "BRZ" and row["cluster_id"] == "007710" for row in frs["structural"])
    assert any(row["make"] == "Scion" and row["model"] == "FR-S" and row["cluster_id"] == "007710" for row in brz["structural"])


def test_acura_rl_has_engine_family_without_structural_siblings(tmp_path: Path) -> None:
    db_path = build_db(tmp_path)
    result = donor_pool("Acura", "RL", 2009, db_path)
    assert result["structural"] == []
    models = {(row["make"], row["model"]) for row in result["engine_family"]}
    assert ("Acura", "TL") in models
    assert ("Acura", "MDX") in models
    assert ("Acura", "ZDX") in models


def test_bmw_528i_returns_structural_and_n52_loop_rows(tmp_path: Path) -> None:
    db_path = build_db(tmp_path)
    result = donor_pool("BMW", "528i", 2008, db_path)
    structural_models = {row["model"] for row in result["structural"]}
    engine_models = {row["model"] for row in result["engine_family"]}
    notes = {row["note"] for row in result["engine_family"]}
    assert "525,530" in structural_models or "M5" in structural_models
    assert {"128i", "328i", "X3 3.0si", "X5 3.0si", "Z4 3.0i"}.issubset(engine_models)
    assert any("335i turbo excluded" in note for note in notes)
    assert any("550i V8 excluded" in note for note in notes)
