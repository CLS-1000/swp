"""G7: pack integrity + spec resolution."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

pytest.importorskip("pandas")

from spw import PACKS_DIR
from spw.gates.pack import PackError, check_pack, load_pack, load_packs
from spw.ingest import ingest_dir
from spw.specs import SpecValue, Vehicle, resolve
from tests.helpers import build_docs

FIX = Path(__file__).parent / "fixtures" / "pack_synthetic.json"


def synth() -> dict:
    return json.loads(FIX.read_text())


def test_shipped_and_fixture_packs_are_sound():
    assert load_packs(PACKS_DIR), "no shipped packs"
    assert load_pack(FIX).id == "synthetic"


def test_shipped_packs_carry_no_literal_specs():
    for pack in load_packs(PACKS_DIR):
        for gate in pack.gates.values():
            assert "literal" not in gate, f"{pack.id}.{gate['id']} embeds a literal spec"


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d["gates"][1]["next"].pop("FAIL"), "next must map exactly"),
        (lambda d: d["gates"][1]["next"].update(PASS="NOWHERE"), "not a gate or TERMINAL"),
        (lambda d: d["gates"][1]["next"].update(FAIL="TERMINAL-UNDEFINED"), "undefined terminal"),
        (lambda d: d["gates"][1].update(condition="WARM"), "condition must be"),
        (lambda d: d["gates"][1].update(instruction="Should read 13.8 volts."), "digits in instruction"),
        (lambda d: d["gates"][3]["literal"].update(cite="nowhere"), "literal needs a cite"),
        (lambda d: (d["gates"][1].pop("spec_ref")), "needs spec_ref"),
        (lambda d: d["gates"][2].update(id="DTCS"), "duplicate gate ids"),
        (lambda d: d.update(start="NOPE"), "start gate"),
        (lambda d: d["gates"][1].update(op="=="), "op in"),
        (lambda d: d["gates"][4]["next"].update(PASS="TERMINAL-MYSTERY", FAIL="TERMINAL-MYSTERY"), "unreachable"),
    ],
)
def test_integrity_failures_are_caught(mutate, needle):
    data = copy.deepcopy(synth())
    mutate(data)
    assert any(needle in p for p in check_pack(data)), check_pack(data)


def test_load_pack_raises_on_bad(tmp_path):
    bad = synth()
    bad["gates"] = []
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(PackError):
        load_pack(p)


@pytest.fixture
def kb(tmp_path):
    build_docs(tmp_path / "docs")
    path = tmp_path / "kb.db"
    ingest_dir(tmp_path / "docs", path)
    return path


GATES = {g["id"]: g for g in synth()["gates"]}
VEH = Vehicle("Synthetic", "Widget", 2003)


def test_table_beats_literal_and_cites(kb):
    r = resolve(GATES["ECUPWR"], VEH, kb)
    assert r.status == "RESOLVED" and r.spec.source == "table"
    assert (r.spec.vmin, r.spec.vmax) == (1.11, 2.22)
    assert r.spec.describe().endswith("[specs.csv p.7]")


def test_literal_fallback_is_cited(kb):
    r = resolve(GATES["WIRE"], VEH, kb)
    assert r.status == "RESOLVED" and r.spec.source == "pack" and r.spec.cite == "synthetic.md p.1"


def test_unverified_when_no_source(kb):
    r = resolve(GATES["MYSTERY"], VEH, kb)
    assert r.status == "UNVERIFIED" and r.spec is None and "No cited spec" in r.note
    op = SpecValue(5.55, None, None, "V", "operator", "operator")
    assert resolve(GATES["MYSTERY"], VEH, kb, operator=op).spec.value == 5.55


def test_unverified_without_a_kb(tmp_path):
    assert resolve(GATES["BATT"], VEH, tmp_path / "missing.db").status == "UNVERIFIED"


def test_vehicle_mismatch_and_unknown_vehicle_do_not_resolve(kb):
    assert resolve(GATES["ECUPWR"], Vehicle("Synthetic", "Widget", 2010), kb).status == "UNVERIFIED"
    assert resolve(GATES["ECUPWR"], Vehicle("Other", "Thing", 2003), kb).status == "UNVERIFIED"
    unknown = resolve(GATES["ECUPWR"], Vehicle(), kb)
    assert unknown.status == "UNVERIFIED" and "vehicle unknown" in unknown.note


def test_conflict_shows_both_and_operator_chooses(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    head = "make,model,years,ecu_family,quantity,value,unit,measurement_state,page\n"
    (docs / "a.csv").write_text(head + ",,,Synthmarelli,Battery floor,3.33,V,CRANKING,1\n")
    (docs / "b.csv").write_text(head + ",,,Synthmarelli,Battery floor,4.44,V,CRANKING,2\n")
    (docs / "c.csv").write_text(head + ",,,Synthmarelli,Battery floor,3.33,V,CRANKING,3\n")
    kb = tmp_path / "kb.db"
    ingest_dir(docs, kb)
    r = resolve(GATES["BATT"], VEH, kb)
    assert r.status == "CONFLICT" and r.spec is None
    assert [c.value for c in r.candidates] == [3.33, 4.44]
    assert "a.csv p.1" in r.candidates[0].cite and "c.csv p.3" in r.candidates[0].cite  # agreeing sources merge
    chosen = resolve(GATES["BATT"], VEH, kb, choice=1)
    assert chosen.status == "RESOLVED" and chosen.spec.value == 4.44


def test_unverified_report_counts(kb, tmp_path):
    from spw.gates.report import unverified_report

    packs = tmp_path / "p"
    packs.mkdir()
    (packs / "s.json").write_text(FIX.read_text())
    # vehicle-specific table rows don't count for the vehicle-agnostic report; the literal does
    rep = unverified_report(packs, kb)[0]
    assert rep["pack"] == "synthetic" and rep["numeric_gates"] == 4
    assert rep["RESOLVED"] == 1 and rep["UNVERIFIED"] == 3
