"""G8: engine fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spw.gates.engine import (
    Engine,
    Reading,
    check_reading,
    commit_line,
    commit_token,
    evaluate,
    parse_commit,
    replay_position,
)
from spw.gates.pack import load_pack
from spw.specs import Resolution, SpecValue

FIX = Path(__file__).parent / "fixtures" / "pack_synthetic.json"
PACK = load_pack(FIX)


def sv(value=None, vmin=None, vmax=None, unit="V"):
    return SpecValue(value, vmin, vmax, unit, "fixture p.1", "table")


def res(**kw):
    s = sv(**kw)
    return Resolution("RESOLVED", s, [s])


FULL_SPECS = {
    "BATT": res(value=3.33),
    "ECUPWR": res(vmin=1.11, vmax=2.22),
    "WIRE": res(value=4.44),
}


def fresh(specs=None):
    e = Engine(PACK, specs=FULL_SPECS if specs is None else specs)
    s = e.new_session()
    e.complete_intake(s)
    return e, s


G = PACK.gates


@pytest.mark.parametrize(
    ("gate", "spec", "value", "expected"),
    [
        ("BATT", sv(value=3.33), 3.33, "PASS"),  # >= is inclusive
        ("BATT", sv(value=3.33), 3.32, "FAIL"),
        ("WIRE", sv(value=4.44), 4.44, "PASS"),  # <= inclusive
        ("WIRE", sv(value=4.44), 4.45, "FAIL"),
        ("WIRE", sv(vmin=1.0, vmax=4.44), 5.0, "FAIL"),  # <= falls back to vmax
        ("BATT", sv(vmin=3.33, vmax=9.0), 3.0, "FAIL"),  # >= falls back to vmin
        ("ECUPWR", sv(vmin=1.11, vmax=2.22), 1.10, "LOW"),
        ("ECUPWR", sv(vmin=1.11, vmax=2.22), 1.11, "PASS"),
        ("ECUPWR", sv(vmin=1.11, vmax=2.22), 2.22, "PASS"),
        ("ECUPWR", sv(vmin=1.11, vmax=2.22), 2.23, "HIGH"),
        ("DTCS", None, True, "YES"),
        ("DTCS", None, False, "NO"),
    ],
)
def test_evaluate(gate, spec, value, expected):
    assert evaluate(G[gate], spec, value) == expected


def test_evaluate_refuses_without_usable_spec():
    with pytest.raises(ValueError):
        evaluate(G["BATT"], None, 12.0)
    with pytest.raises(ValueError):
        evaluate(G["ECUPWR"], sv(value=2.0), 2.0)  # window needs both bounds


def test_happy_path_in_order():
    e, s = fresh()
    steps = [Reading(True), Reading(5.0), Reading(1.5), Reading(4.0)]
    tokens = []
    for r in steps:
        ev, err = e.record(s, r)
        assert err is None
        tokens += [x.commit for x in ev]
    assert tokens == [
        "DEV-BRANCH-GATE-DTCS-YES",
        "DEV-BRANCH-GATE-BATT-PASS",
        "DEV-BRANCH-GATE-ECUPWR-PASS",
        "DEV-BRANCH-GATE-WIRE-PASS",
    ]
    assert s.current == "MYSTERY"


def test_branch_is_the_engines_not_the_callers():
    e, s = fresh()
    e.record(s, Reading(True))
    ev, _ = e.record(s, Reading(1.0))  # fail the battery
    assert [x.commit for x in ev][-2:] == ["DEV-BRANCH-GATE-BATT-FAIL", "DEV-BRANCH-TERMINAL-BATT_LOW"]
    assert e.status(s).kind == "TERMINAL" and e.status(s).terminal == "BATT_LOW"
    assert e.record(s, Reading(9.0))[1] == "Not ready for a reading yet."


def test_missing_reading_halts_and_asks_plainly():
    e, s = fresh()
    e.record(s, Reading(True))
    st = e.status(s)
    assert st.kind == "NEED_READING" and st.commit == "DEV-BRANCH-HALT-NO-TELEMETRY"
    assert "Need the battery voltage, cranking." in st.text
    assert s.current == "BATT"


def test_unverified_spec_blocks_until_operator_supplies():
    e, s = fresh({})  # nothing resolves
    e.record(s, Reading(True))
    ev, _ = e.record(s, Reading(11.0))
    assert ev == [] and s.current == "BATT"
    st = e.status(s)
    assert st.kind == "NEED_SPEC" and "No cited spec — confirm from your manual" in st.text
    ev = e.set_operator_spec(s, sv(value=3.33, unit="V"))
    assert ev[0].commit == "DEV-BRANCH-GATE-BATT-PASS"


def test_reading_state_and_unit_validation():
    e, s = fresh()
    e.record(s, Reading(True))
    assert "not key on, engine off" in e.record(s, Reading(5.0, "V", "KOEO"))[1]
    assert "you gave ohm" in e.record(s, Reading(5.0, "ohm", "CRANKING"))[1]
    assert "needs a V reading" in e.record(s, Reading(True))[1]
    assert s.current == "BATT"
    assert check_reading(G["DTCS"], Reading(5.0)) == "That step is a yes/no check."


def test_state_defaults_to_the_gates_condition():
    e, s = fresh()
    e.record(s, Reading(True))
    e.record(s, Reading(5.0))
    assert s.readings["BATT"].state == "CRANKING"


def at(e, s, gate):
    s.current = gate
    return e.status(s)


def test_risk_step_blocked_until_ack():
    e, s = fresh({})
    st = at(e, s, "PROBE")
    assert st.kind == "NEED_ACK" and "RISK FLAG" in st.text and "ack" in st.text
    assert st.commit == "DEV-BRANCH-HALT-NO-TELEMETRY"
    # a reading offered early is held, not applied
    ev, err = e.hold(s, "PROBE", Reading(True))
    assert err is None and [x.commit for x in ev] == ["DEV-BRANCH-PENDING-PROBE"]
    assert "PROBE" not in s.outcomes
    ev = e.ack(s)
    assert [x.commit for x in ev] == [
        "DEV-BRANCH-RISK-ACK-PROBE",
        "DEV-BRANCH-GATE-PROBE-YES",
        "DEV-BRANCH-TERMINAL-COMMS_OK",
    ]


def test_risk_not_shown_twice_and_record_refused_pre_ack():
    e, s = fresh({})
    at(e, s, "PROBE")
    assert e.record(s, Reading(True))[1] == "Not ready for a reading yet."
    e.ack(s)
    assert e.ack(s) == []


def test_power_cut_requires_dtc_capture():
    e, s = fresh({})
    st = at(e, s, "GND")
    assert st.kind == "NEED_DTCS" and "record all DTCs" in st.text
    assert e.record(s, Reading(False))[1] == "Not ready for a reading yet."
    e.mark_dtcs_captured(s)
    assert e.status(s).kind == "NEED_READING"


def test_dtc_gate_yes_marks_captured():
    e, s = fresh()
    e.record(s, Reading(True))
    assert s.dtcs_captured
    e2, s2 = fresh()
    e2.record(s2, Reading(False))
    assert not s2.dtcs_captured and s2.current == "TERMINAL-CAPTURE_DTCS"


def test_pending_applies_in_order_when_reached():
    e, s = fresh()
    ev, err = e.hold(s, "ECUPWR", Reading(1.5, "V", "KOEO"))
    assert err is None and [x.commit for x in ev] == ["DEV-BRANCH-PENDING-ECUPWR"]
    assert s.current == "DTCS"
    ev, _ = e.record(s, Reading(True))  # DTCS -> BATT (BATT still needs its own reading)
    assert s.current == "BATT" and "ECUPWR" not in s.outcomes
    ev, _ = e.record(s, Reading(5.0))
    assert [x.commit for x in ev] == ["DEV-BRANCH-GATE-BATT-PASS", "DEV-BRANCH-GATE-ECUPWR-PASS"]
    assert s.current == "WIRE"


def test_pending_validation():
    e, s = fresh()
    assert e.hold(s, "NOPE", Reading(1.0))[1] == "Nothing to hold that for."
    assert e.hold(s, "ECUPWR", Reading(1.0, "V", "RUNNING"))[1].startswith("That step is measured key on, engine off")


def test_commit_round_trip_and_replay():
    e, s = fresh()
    for r in [Reading(True), Reading(5.0), Reading(1.5), Reading(4.0), Reading(0.5)]:
        e.record(s, r)
    tokens = [ev.commit for ev in s.ledger]
    for tok in tokens:
        kind, gate, out = parse_commit(commit_line(tok))
        assert commit_token(kind, gate, out) == tok
    gate_tokens = [t for t in tokens if "-GATE-" in t]
    assert replay_position(PACK, gate_tokens) == s.current == "MYSTERY"


@pytest.mark.parametrize(
    "tok",
    [
        commit_token("INTAKE"),
        commit_token("HALT"),
        commit_token("RISK-ACK", "PROBE"),
        commit_token("PENDING", "ECUPWR"),
        commit_token("TERMINAL", "COMMS_OK"),
        commit_token("GATE", "BATT", "PASS"),
        commit_token("GATE", "ECUPWR", "HIGH"),
    ],
)
def test_vocabulary_round_trips(tok):
    kind, gate, out = parse_commit(commit_line(tok))
    assert commit_token(kind, gate, out) == tok


def test_bad_commit_rejected():
    assert parse_commit("COMMIT: DEV-BRANCH-GATE-BATT-MAYBE") is None
    assert parse_commit("COMMIT: GATE-BATT-PASS") is None


def test_shipped_pack_runs_with_no_specs_and_never_invents():
    from spw import PACKS_DIR

    pack = load_pack(Path(PACKS_DIR) / "no_start_crank.json")
    e = Engine(pack, kb_path="/nonexistent/kb.db")
    s = e.new_session()
    e.complete_intake(s)
    e.record(s, Reading(True))
    e.record(s, Reading(12.0))
    st = e.status(s)
    assert st.kind == "NEED_SPEC" and s.current == "BATT"
    assert "12" not in json.dumps(st.text)
