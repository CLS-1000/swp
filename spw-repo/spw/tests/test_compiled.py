"""Gate 7: compiled overlay integrity + modern-vehicle lookup fixtures.

These tests build their database from the compiled CSVs alone, so they run
without pdftotext or the EDC-1057 PDF.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from spw.compiled import apply_compiled, BASE_VEHICLE_ID
from spw.parse import SCHEMA
from spw.lookup import donor_pool, norm_make, norm_model

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SHIPPED_DB = ROOT / "clone_clusters.db"

VALID_CONSTRUCTION = {"u", "f", ""}
VALID_DRIVE_TOKENS = {"f", "r", "4", "a"}


def _rows(name: str) -> list[dict]:
    with (ASSETS / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    """Compiled-only database — no EDC rows."""
    db = tmp_path_factory.mktemp("compiled") / "compiled.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.close()
    stats = apply_compiled(db, ASSETS)
    assert stats["unknown_clusters"] == [], stats["unknown_clusters"]
    return db


# ---- source data integrity ----

class TestCsvIntegrity:
    def test_cluster_ids_unique(self):
        ids = [r["cluster_id"] for r in _rows("compiled_clusters.csv")]
        assert len(ids) == len(set(ids)), "duplicate cluster_id in compiled_clusters.csv"

    def test_cluster_types_known(self):
        for r in _rows("compiled_clusters.csv"):
            assert r["type"] in ("structural", "engine_family"), r

    def test_vehicle_fields_sane(self):
        for r in _rows("compiled_vehicles.csv"):
            where = f"{r['make']} {r['model']}"
            assert r["make"] and r["model"] and r["styles"], where
            y0, y1 = int(r["year_start"]), int(r["year_end"])
            assert 1990 <= y0 <= y1 <= 2030, where
            assert r["construction"] in VALID_CONSTRUCTION, where
            tokens = {t for t in r["drive"].replace("/", ",").split(",") if t}
            assert tokens <= VALID_DRIVE_TOKENS, f"{where}: drive {r['drive']}"
            if r["wheelbase"]:
                assert 80.0 < float(r["wheelbase"]) < 200.0, where

    def test_legacy_year_ranges_sane(self):
        for r in _rows("compiled_legacy.csv"):
            y0, y1 = int(r["year_from"]), int(r["year_to"])
            assert 1974 <= y0 <= y1 <= 2030, r

    def test_every_referenced_cluster_is_defined(self, db_path):
        # apply_compiled reports dangling references rather than failing loudly
        stats = apply_compiled(db_path, ASSETS)
        assert stats["unknown_clusters"] == []


# ---- load behaviour ----

class TestLoad:
    def test_counts(self, db_path):
        con = sqlite3.connect(str(db_path))
        vehicles = con.execute(
            "SELECT COUNT(*) FROM vehicles WHERE source='compiled'"
        ).fetchone()[0]
        clusters = con.execute(
            "SELECT COUNT(*) FROM clusters WHERE source='compiled'"
        ).fetchone()[0]
        engines = con.execute(
            "SELECT COUNT(*) FROM clusters WHERE source='compiled' AND type='engine_family'"
        ).fetchone()[0]
        con.close()
        assert vehicles >= 300
        assert clusters >= 140
        assert engines >= 80

    def test_reserved_id_range(self, db_path):
        con = sqlite3.connect(str(db_path))
        low = con.execute(
            "SELECT MIN(id) FROM vehicles WHERE source='compiled'"
        ).fetchone()[0]
        con.close()
        assert low >= BASE_VEHICLE_ID

    def test_coverage_reaches_current_model_years(self, db_path):
        con = sqlite3.connect(str(db_path))
        newest = con.execute("SELECT MAX(year_end) FROM vehicles").fetchone()[0]
        con.close()
        assert newest >= 2025, "compiled overlay should cover current model years"

    def test_idempotent(self, db_path):
        first = apply_compiled(db_path, ASSETS)
        second = apply_compiled(db_path, ASSETS)
        con = sqlite3.connect(str(db_path))
        n = con.execute("SELECT COUNT(*) FROM membership").fetchone()[0]
        con.close()
        assert first["memberships"] == second["memberships"]
        assert n == second["memberships"] + second["legacy_links"]

    def test_all_memberships_tagged_compiled(self, db_path):
        con = sqlite3.connect(str(db_path))
        tiers = {r[0] for r in con.execute("SELECT DISTINCT confidence FROM membership")}
        con.close()
        assert tiers == {"compiled"}


# ---- interaction with the EDC and curated tiers ----

@pytest.mark.skipif(not SHIPPED_DB.exists(), reason="shipped database not present")
class TestTierIsolation:
    def test_edc_and_curated_rows_survive(self, tmp_path):
        db = tmp_path / "mixed.db"
        db.write_bytes(SHIPPED_DB.read_bytes())
        con = sqlite3.connect(str(db))
        before = dict(con.execute(
            "SELECT confidence, COUNT(*) FROM membership "
            "WHERE confidence<>'compiled' GROUP BY 1"
        ).fetchall())
        edc_vehicles = con.execute(
            "SELECT COUNT(*) FROM vehicles WHERE source='EDC-1057'"
        ).fetchone()[0]
        con.close()

        apply_compiled(db, ASSETS)

        con = sqlite3.connect(str(db))
        after = dict(con.execute(
            "SELECT confidence, COUNT(*) FROM membership "
            "WHERE confidence<>'compiled' GROUP BY 1"
        ).fetchall())
        edc_after = con.execute(
            "SELECT COUNT(*) FROM vehicles WHERE source='EDC-1057'"
        ).fetchone()[0]
        con.close()
        assert after == before
        assert edc_after == edc_vehicles

    def test_legacy_rules_all_match_something(self, tmp_path):
        db = tmp_path / "mixed.db"
        db.write_bytes(SHIPPED_DB.read_bytes())
        stats = apply_compiled(db, ASSETS)
        assert stats["empty_rules"] == [], stats["empty_rules"]
        assert stats["legacy_links"] >= 300

    def test_engine_coverage_improved(self, tmp_path):
        db = tmp_path / "mixed.db"
        db.write_bytes(SHIPPED_DB.read_bytes())
        apply_compiled(db, ASSETS)
        con = sqlite3.connect(str(db))
        linked = con.execute(
            """SELECT COUNT(DISTINCT m.vehicle_id) FROM membership m
               JOIN clusters c ON c.cluster_id=m.cluster_id
               WHERE c.type='engine_family'"""
        ).fetchone()[0]
        con.close()
        # the pre-overlay dataset linked 30 vehicles to an engine family
        assert linked >= 500


# ---- normalisation ----

class TestNormalisation:
    def test_make_alias(self):
        assert norm_make("Mercedes-Benz") == "mercedes"
        assert norm_make(" TOYOTA ") == "toyota"

    def test_model_separators_fold(self):
        assert norm_model("3-Series") == norm_model("3 Series") == "3 series"
        assert norm_model("F-150") == "f 150"


# ---- lookup fixtures on modern vehicles ----

class TestModernLookups:
    def test_rav4_platform_and_engine(self, db_path):
        donors = donor_pool("Toyota", "RAV4", 2021, db_path)
        structural = {d.model for d in donors if d.cluster_type == "structural"}
        engine = {d.model for d in donors if d.cluster_type == "engine_family"}
        assert {"Camry", "Highlander"} <= structural, structural
        assert "Camry" in engine, engine

    def test_silverado_shares_sierra_and_tahoe(self, db_path):
        donors = donor_pool("Chevrolet", "Silverado 1500", 2016, db_path)
        structural = {(d.make, d.model) for d in donors if d.cluster_type == "structural"}
        assert ("GMC", "Sierra 1500") in structural, structural
        assert ("Chevrolet", "Tahoe") in structural, structural

    def test_wrangler_pentastar_reaches_minivans(self, db_path):
        donors = donor_pool("Jeep", "Wrangler", 2019, db_path)
        engine = {(d.make, d.model) for d in donors if d.cluster_type == "engine_family"}
        assert ("Chrysler", "Pacifica") in engine, engine

    def test_supra_reaches_bmw_z4(self, db_path):
        donors = donor_pool("Toyota", "Supra", 2021, db_path)
        models = {(d.make, d.model) for d in donors}
        assert ("BMW", "Z4") in models, models

    def test_brz_and_gr86_share_engine(self, db_path):
        donors = donor_pool("Subaru", "BRZ", 2023, db_path)
        engine = {(d.make, d.model) for d in donors if d.cluster_type == "engine_family"}
        assert ("Toyota", "GR86") in engine, engine

    def test_mercedes_benz_make_alias_resolves(self, db_path):
        donors = donor_pool("Mercedes-Benz", "GLC300", 2019, db_path)
        assert donors, "Mercedes-Benz should alias onto the dataset's 'Mercedes' rows"

    def test_bmw_series_form_matches(self, db_path):
        donors = donor_pool("BMW", "3 Series", 2019, db_path)
        assert donors, "'3 Series' from vPIC should match the dataset's 3-Series rows"

    def test_trim_exclusion_notes_present(self, db_path):
        donors = donor_pool("Chevrolet", "Silverado 1500", 2016, db_path)
        engine = [d for d in donors if d.cluster_type == "engine_family"]
        assert any("excluded" in (d.note or "").lower() for d in engine), \
            "engine-family donors must carry trim-exclusion qualifiers"

    def test_engine_memberships_are_mostly_qualified(self, db_path):
        """A bare engine-family link is dangerous — most need a trim qualifier."""
        con = sqlite3.connect(str(db_path))
        total, qualified = con.execute(
            """SELECT COUNT(*), SUM(CASE WHEN IFNULL(m.note,'')<>'' THEN 1 ELSE 0 END)
               FROM membership m JOIN clusters c ON c.cluster_id=m.cluster_id
               WHERE c.type='engine_family'"""
        ).fetchone()
        con.close()
        assert qualified / total > 0.9, f"only {qualified}/{total} engine links qualified"

    def test_qualifier_falls_back_to_cluster_label(self, db_path):
        donors = donor_pool("Toyota", "RAV4", 2021, db_path)
        structural = [d for d in donors if d.cluster_type == "structural"]
        assert structural
        assert all(d.qualifier != "—" for d in structural)
