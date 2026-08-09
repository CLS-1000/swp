"""Gate 5: Lookup fixtures + regression tests."""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from spw.parse import parse, apply_curated, SCHEMA
from spw.validate import validate
from spw.lookup import donor_pool
from spw.export import to_json, to_csv

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
PDF = ASSETS / "EDC-1057.pdf"
CURATED = ASSETS / "curated.sql"


@pytest.fixture(scope="session")
def db_path(tmp_path_factory):
    """Build a fresh database from source for testing."""
    db = tmp_path_factory.mktemp("data") / "test.db"
    # create schema first
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.close()
    stats = parse(PDF, db)
    assert stats["skipped_ratio"] <= 0.01, f"Gate 1: skipped ratio {stats['skipped_ratio']:.2%} > 1%"
    assert stats["unique"] >= 1800, f"Gate 1: unique {stats['unique']} < 1800"
    apply_curated(db, CURATED)
    validate(db)
    return db


# ---- Gate 1: Parse integrity ----

class TestGate1:
    def test_vehicle_count(self, db_path):
        con = sqlite3.connect(str(db_path))
        n = con.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
        con.close()
        assert n >= 1800

    def test_structural_clusters(self, db_path):
        con = sqlite3.connect(str(db_path))
        n = con.execute("SELECT COUNT(*) FROM clusters WHERE type='structural'").fetchone()[0]
        con.close()
        assert n >= 900


# ---- Gate 2: Curated immutability ----

class TestGate2:
    def test_curated_clusters_exist(self, db_path):
        con = sqlite3.connect(str(db_path))
        for cid in ["J37-LOOP", "J35-ADJ", "N52-LOOP"]:
            row = con.execute(
                "SELECT source FROM clusters WHERE cluster_id=?", (cid,)
            ).fetchone()
            assert row is not None, f"Curated cluster {cid} missing"
            assert row[0] == "curated", f"Cluster {cid} source is {row[0]}, expected curated"
        con.close()

    def test_n52_memberships(self, db_path):
        con = sqlite3.connect(str(db_path))
        n = con.execute(
            "SELECT COUNT(*) FROM membership WHERE cluster_id='N52-LOOP'"
        ).fetchone()[0]
        con.close()
        assert n >= 10, f"N52-LOOP has only {n} memberships"


# ---- Gate 3: Dedupe ----

class TestGate3:
    def test_no_duplicate_vehicles(self, db_path):
        """Dedupe key includes cluster membership — same car with different
        Clone IDs is valid EDC data (appears in multiple structural groups)."""
        con = sqlite3.connect(str(db_path))
        dupes = con.execute(
            """SELECT v.make,v.model,v.styles,v.year_start,v.year_end,
                      m.cluster_id,COUNT(*) n
               FROM vehicles v
               LEFT JOIN membership m ON m.vehicle_id=v.id AND m.scope='structural'
               GROUP BY v.make,v.model,v.styles,v.year_start,v.year_end,m.cluster_id
               HAVING n > 1"""
        ).fetchall()
        con.close()
        assert len(dupes) == 0, f"Found {len(dupes)} duplicate (vehicle,cluster) tuples"


# ---- Gate 4: Consistency validation ----

class TestGate4:
    def test_flags_logged(self, db_path):
        con = sqlite3.connect(str(db_path))
        n = con.execute(
            "SELECT COUNT(*) FROM verdicts WHERE method='consistency-check'"
        ).fetchone()[0]
        con.close()
        assert n >= 50, f"Only {n} consistency flags — expected ≥50"


# ---- Gate 5: Lookup fixtures ----

class TestGate5:
    def test_tahoe_escalade_yukon(self, db_path):
        donors = donor_pool("Chevrolet", "Tahoe", 2010, db_path)
        models = {(d.make, d.model) for d in donors if d.cluster_type == "structural"}
        assert ("Cadillac", "Escalade") in models or any(
            "Escalade" in d.model for d in donors if d.cluster_type == "structural"
        ), f"Escalade not in Tahoe structural pool: {models}"
        assert ("GMC", "Yukon") in models or any(
            "Yukon" in d.model for d in donors if d.cluster_type == "structural"
        ), f"Yukon not in Tahoe structural pool: {models}"

    def test_frs_brz(self, db_path):
        donors_frs = donor_pool("Scion", "FR-S", 2013, db_path)
        brz = [d for d in donors_frs if "BRZ" in d.model]
        assert len(brz) > 0, "BRZ not in FR-S donor pool"

    def test_acura_rl(self, db_path):
        donors = donor_pool("Acura", "RL", 2009, db_path)
        structural = [d for d in donors if d.cluster_type == "structural"]
        engine = [d for d in donors if d.cluster_type == "engine_family"]
        # RL should have no structural siblings with different model names
        other_models = {d.model for d in structural if "RL" not in d.model}
        assert len(other_models) == 0, f"RL has unexpected structural siblings: {other_models}"
        # engine family should include TL, MDX, ZDX
        eng_models = {d.model for d in engine}
        for expected in ["TL", "MDX", "ZDX"]:
            assert any(expected in m for m in eng_models), f"{expected} missing from RL J37-LOOP"

    def test_bmw_528i(self, db_path):
        donors = donor_pool("BMW", "528i", 2008, db_path)
        structural = [d for d in donors if d.cluster_type == "structural"]
        engine = [d for d in donors if d.cluster_type == "engine_family"]
        # structural: other E60 sedan rows
        assert len(structural) > 0, "No structural donors for 528i"
        # engine: N52-LOOP members
        assert len(engine) > 0, "No engine family donors for 528i"
        eng_models = {d.model for d in engine}
        # must include 3-Series/328 variants
        assert any("328" in m or "3-Series" in m or "325" in m for m in eng_models), \
            f"3-Series missing from 528i N52-LOOP: {eng_models}"
        # turbo exclusion notes must be present
        turbo_notes = [d for d in engine if "turbo" in (d.note or "").lower() or "excluded" in (d.note or "").lower()]
        assert len(turbo_notes) > 0, "No turbo exclusion notes in N52-LOOP donors"


# ---- Gate 6: Export ----

class TestGate6:
    def test_json_export(self, db_path):
        data = to_json(db_path)
        assert len(data) > 1000
        import json
        parsed = json.loads(data)
        assert "v" in parsed and "m" in parsed and "c" in parsed

    def test_csv_export(self, db_path, tmp_path):
        csv_out = tmp_path / "test.csv"
        n = to_csv(db_path, csv_out)
        assert n > 1000
        assert csv_out.exists()
