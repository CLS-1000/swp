"""G10: grounding. Scripted transcripts against a stubbed LLM, and the same transcript offline."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("pandas")

from spw.audit import audit_numbers
from spw.chat import ChatSession
from spw.extract import extract, parse_vehicle
from spw.gates.engine import parse_commit
from spw.gates.pack import load_pack
from spw.ingest import ingest_dir
from spw.llm import LLMError
from tests.helpers import StubLLM, build_docs

FIX = Path(__file__).parent / "fixtures" / "pack_synthetic.json"
PACK = load_pack(FIX)

EXTRA_MD = """# CAN notes

CAN bus termination: the synthetic bus is terminated at both ends. See the termination section.
"""


@pytest.fixture
def kb(tmp_path):
    docs = tmp_path / "docs"
    build_docs(docs)
    (docs / "can.md").write_text(EXTRA_MD)
    path = tmp_path / "kb.db"
    ingest_dir(docs, path)
    return path


def chat(kb, tmp_path, llm=None, **kw):
    return ChatSession([PACK], kb, llm=llm, diag_db=tmp_path / "diag.db", **kw)


# ---- extractor ------------------------------------------------------------------------------
def test_extractor_parses_about_12_3_cranking():
    ex = extract("about 12.3 cranking")
    assert len(ex.readings) == 1 and ex.approx
    r = ex.readings[0]
    assert (r.value, r.unit, r.state) == (12.3, None, "CRANKING")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12.3 V cranking", [(12.3, "V", "CRANKING")]),
        ("battery read about 12.3", [(12.3, None, None)]),
        ("1.5 volts key on", [(1.5, "V", "KOEO")]),
        ("120 ohms key off", [(120.0, "ohm", "KEY_OFF")]),
        ("it was 13.9 running, 12.1 cranking", [(13.9, None, "RUNNING"), (12.1, None, "CRANKING")]),
        ("2008 BMW 528i won't start", []),  # year and badge are not readings
        ("P0300 and U0100 stored", []),  # DTC digits are not readings
        ("the 3rd time it died after about 20 minutes of idle", []),  # too chatty, no unit, no cue
    ],
)
def test_extractor_cases(text, expected):
    got = [(r.value, r.unit, r.state) for r in extract(text).readings]
    assert got == expected


def test_extractor_dtcs_and_vehicle():
    assert extract("codes P0300 and u0100").dtcs == ["P0300", "U0100"]
    assert parse_vehicle("2008 BMW 528i cranks but won't fire") == ("BMW", "528i", 2008)
    assert parse_vehicle("2000 ohms across the pair") is None


# ---- the transcript ---------------------------------------------------------------------------
TRANSCRIPT = [
    "2003 Synthetic Widget cranks but won't fire, battery read about 12.3",
    "yes",
    "P0300 and U0100",
    "yes",
    "yes",
    "1.5 volts key on",
    "yes",
]


def run(c, script=TRANSCRIPT):
    return [c.turn(t) for t in script]


def snapshot(c):
    s = c.state
    return {
        "current": s.current,
        "outcomes": dict(s.outcomes),
        "readings": {k: v.value for k, v in s.readings.items()},
        "pending": dict(s.pending),
        "dtcs": list(s.dtcs),
    }


def explain_clean(system, user):
    if "OPERATOR ASKED" in user:
        return "Termination is covered in the CAN notes [can.md p.1]."
    return "Take the reading where the step says. What do you get?"


def test_nothing_recorded_before_confirmation_and_engine_picks_the_branch(kb, tmp_path):
    c = chat(kb, tmp_path, StubLLM(explain_clean))
    t1 = c.turn(TRANSCRIPT[0])
    assert "Logging 12.3 V, cranking" in t1.reply and t1.reply.rstrip().endswith("SAFEGUARD: Nothing is logged until you confirm.")
    assert t1.commit == "DEV-BRANCH-HALT-NO-TELEMETRY"
    assert c.state.pending == {} and c.state.readings == {} and c.state.outcomes == {}
    t2 = c.turn("yes")
    assert t2.commit == "DEV-BRANCH-PENDING-BATT"
    assert "BATT" in c.state.pending and c.state.current == "DTCS" and c.state.outcomes == {}
    c.turn("P0300 and U0100")
    assert c.state.dtcs == []  # echoed, not yet logged
    c.turn("yes")
    assert c.state.dtcs == ["P0300", "U0100"]
    t5 = c.turn("yes")  # DTCs captured -> pending BATT reading applies -> engine advances
    assert [e.commit for e in t5.events] == ["DEV-BRANCH-GATE-DTCS-YES", "DEV-BRANCH-GATE-BATT-PASS"]
    assert t5.commit == "DEV-BRANCH-GATE-BATT-PASS" and c.state.current == "ECUPWR"
    assert "Battery floor: pass (12.3 V vs >= 3.33 V [specs.csv p.8])" in t5.reply
    t6 = c.turn("1.5 volts key on")
    assert c.state.current == "ECUPWR" and "ECUPWR" not in c.state.readings
    t7 = c.turn("yes")
    assert t7.commit == "DEV-BRANCH-GATE-ECUPWR-PASS" and c.state.current == "WIRE"
    assert t6.commit == "DEV-BRANCH-HALT-NO-TELEMETRY"


def test_wrong_confirmation_discards(kb, tmp_path):
    c = chat(kb, tmp_path)
    c.turn(TRANSCRIPT[0])
    t = c.turn("no")
    assert "Scratched that" in t.reply and c.state.pending == {} and c.awaiting is None


def test_llm_never_picks_the_branch(kb, tmp_path):
    def bossy(system, user):
        return "Skip ahead to the ground check. What do you get?"

    c = chat(kb, tmp_path, StubLLM(bossy))
    run(c)
    assert c.state.current == "WIRE"  # engine order, whatever the model said


def test_rogue_numbers_outcomes_citations_and_double_questions_are_rejected(kb, tmp_path):
    rogue = [
        "The battery should read 13.8 volts. What do you get?",
        "Roughly thirteen volts is normal. What do you get?",
        "Check [fake.pdf p.9] first. What do you get?",
        "That's a pass already. What do you get?",
        "What do you see? And what else?",
    ]
    for body in rogue:
        c = chat(kb, tmp_path, StubLLM(lambda s, u, b=body: b))
        t = c.turn("2003 Synthetic Widget cranks but won't fire")
        assert t.audit_rejected, body
        assert not t.used_llm and body.split(".")[0] not in t.reply
        assert "Record all codes. (yes/no)" in t.reply  # deterministic fallback
        assert audit_numbers(t.reply, t.allowed) == []


def test_number_audit_over_the_whole_transcript(kb, tmp_path):
    for llm in (StubLLM(explain_clean), None):
        c = chat(kb, tmp_path, llm)
        for turn, said in zip(run(c), TRANSCRIPT, strict=True):
            assert audit_numbers(turn.reply, turn.allowed) == [], (said, turn.reply)


def test_audit_unit():
    allowed = ["Spec 3.33 V [specs.csv p.8]", "operator said 12.3"]
    assert audit_numbers("Reads 12.30 and spec is 3.33 V [specs.csv p.8].", allowed) == []
    assert audit_numbers("Should be 13.8 V.", allowed) == ["13.8"]
    assert audit_numbers("About twelve volts.", allowed) == ["twelve volts"]
    assert audit_numbers("See [x.pdf p.3].", allowed) == ["[x.pdf p.3]"]
    assert audit_numbers("1. First do this\n2) then that", allowed) == []  # list markers are not numbers


def test_offline_mode_completes_the_same_transcript(kb, tmp_path):
    online = chat(kb, tmp_path, StubLLM(explain_clean))
    run(online)
    offline = chat(kb, tmp_path, None)
    turns = run(offline)
    assert snapshot(offline) == snapshot(online)
    assert offline.mode == "offline" and online.mode == "llm"
    assert all(not t.used_llm for t in turns)
    assert "Meter the battery posts while cranking." not in turns[0].reply  # still waiting on the echo
    assert "Meter the drop across the supply wire" in turns[-1].reply  # verbatim gate prompt, no LLM phrasing


def test_network_failure_degrades_to_deterministic(kb, tmp_path):
    class Down(StubLLM):
        def explain(self, system, user):
            raise LLMError("offline")

        def extract(self, text):
            raise LLMError("offline")

    c = chat(kb, tmp_path, Down())
    run(c)
    ref = chat(kb, tmp_path, None)
    run(ref)
    assert snapshot(c) == snapshot(ref)


def test_risk_step_blocked_until_ack(kb, tmp_path):
    stub = StubLLM(explain_clean)
    c = chat(kb, tmp_path, stub)
    c.turn("2003 Synthetic Widget cranks but won't fire")
    c.state.current = "PROBE"
    for said in ("yes", "yes please", "go ahead"):
        t = c.turn(said)
        assert "RISK FLAG" in t.reply and "Back-probe the bus line" not in t.reply
        assert t.commit == "DEV-BRANCH-HALT-NO-TELEMETRY" and "PROBE" not in c.state.outcomes
    t = c.turn("ack")
    assert [e.commit for e in t.events] == ["DEV-BRANCH-RISK-ACK-PROBE"]
    assert "Back-probe the bus line" in t.reply  # engine's words verbatim
    assert not any("Back-probe" in u for _, u in stub.explain_calls)  # model never phrased the risk step
    t = c.turn("yes")
    assert [e.commit for e in t.events][-1] == "DEV-BRANCH-TERMINAL-COMMS_OK"


def test_missing_telemetry_is_asked_plainly(kb, tmp_path):
    c = chat(kb, tmp_path)
    c.turn("2003 Synthetic Widget cranks but won't fire")
    c.turn("yes")
    t = c.turn("what now")
    assert "Need the battery voltage, cranking." in t.reply and t.commit == "DEV-BRANCH-HALT-NO-TELEMETRY"
    assert c.state.current == "BATT"


def test_wrong_condition_is_refused_not_recorded(kb, tmp_path):
    c = chat(kb, tmp_path)
    c.turn("2003 Synthetic Widget cranks but won't fire")
    c.turn("yes")  # DTCS -> BATT
    t = c.turn("12.3 V key on")
    c.turn("yes")
    assert c.state.readings.get("BATT") is None
    assert "BATT" not in c.state.outcomes and t.commit == "DEV-BRANCH-HALT-NO-TELEMETRY"


def test_general_question_cites_then_returns_to_step(kb, tmp_path):
    for llm in (StubLLM(explain_clean), None):
        c = chat(kb, tmp_path, llm)
        c.turn("2003 Synthetic Widget cranks but won't fire")
        t = c.turn("what does the manual say about CAN bus termination?")
        assert t.context == "CHAT-Q" and "[can.md p.1]" in t.reply
        assert "Record all codes. (yes/no)" in t.reply or "Take the reading" in t.reply or "codes" in t.reply
        assert audit_numbers(t.reply, t.allowed) == []
        assert c.state.current == "DTCS"
    t = c.turn("how do I bleed the brakes?")
    assert "Nothing in your docs on that" in t.reply


def test_vehicle_asked_once_when_a_step_needs_it(kb, tmp_path):
    c = chat(kb, tmp_path)
    c.turn("cranks but won't fire")
    t = c.turn("yes")  # DTCs captured -> BATT needs a spec; vehicle unknown
    assert "What vehicle is this?" in t.reply and c.awaiting == "vehicle"
    t = c.turn("unknown")
    assert "What vehicle" not in t.reply and "Vehicle noted as unknown" in t.reply
    t = c.turn("what now")
    assert "What vehicle" not in t.reply
    assert "UNVERIFIED" in t.reply and "Nothing in your docs" not in t.reply  # a nudge isn't a docs question


def test_vehicle_volunteered_later_improves_specs(kb, tmp_path):
    c = chat(kb, tmp_path)
    c.turn("cranks but won't fire")
    c.turn("yes")
    c.turn("it's a 2003 Synthetic Widget")
    t = c.turn("what now")
    assert "Spec (cranking): >= 3.33 V [specs.csv p.8]" in t.reply


def test_unverified_step_takes_operator_spec_only_after_confirmation(kb, tmp_path):
    c = chat(kb, tmp_path)
    c.turn("2003 Synthetic Widget cranks but won't fire")
    c.state.current = "MYSTERY"
    t = c.turn("2.0 volts key on")
    assert "Logging 2 V" in t.reply
    t = c.turn("yes")
    assert "No cited spec — confirm from your manual" in t.reply and c.state.current == "MYSTERY"
    t = c.turn("5.55")
    assert "your spec" in t.reply and "MYSTERY" not in c.state.operator_specs
    t = c.turn("yes")
    assert t.events[0].commit == "DEV-BRANCH-GATE-MYSTERY-PASS"


def test_llm_extraction_is_echoed_not_trusted(kb, tmp_path):
    def ext(text):
        return {"readings": [{"value": 12.3, "unit": "V", "state": "CRANKING"}], "dtcs": [], "yes_no": None}

    stub = StubLLM(explain_clean, ext)
    c = chat(kb, tmp_path, stub)
    c.turn("2003 Synthetic Widget cranks but won't fire")
    c.turn("yes")
    t = c.turn("battery was twelve point three while cranking")
    assert stub.extract_calls == ["battery was twelve point three while cranking"]
    assert "Logging 12.3 V, cranking" in t.reply
    assert "BATT" not in c.state.readings and "BATT" not in c.state.pending
    t = c.turn("yes")
    assert t.events[0].commit == "DEV-BRANCH-GATE-BATT-PASS"


def test_regex_goes_first_llm_extractor_not_called(kb, tmp_path):
    stub = StubLLM(explain_clean)
    c = chat(kb, tmp_path, stub)
    c.turn("2003 Synthetic Widget cranks but won't fire")
    c.turn("yes")
    c.turn("about 12.3 cranking")
    assert stub.extract_calls == []


def test_no_matching_flow_stays_in_intake(kb, tmp_path):
    c = chat(kb, tmp_path)
    t = c.turn("water in the oil")
    assert t.commit == "DEV-BRANCH-INTAKE" and "flow for that symptom" in t.reply
    t = c.turn("2003 Synthetic Widget cranks but won't fire")
    assert c.engine is not None


def test_every_turn_logs_commit_and_safeguard_to_diag_events(kb, tmp_path):
    c = chat(kb, tmp_path, StubLLM(explain_clean))
    turns = run(c) + [c.turn("what does the manual say about CAN bus termination?")]
    with sqlite3.connect(tmp_path / "diag.db") as conn:
        rows = conn.execute("SELECT turn, context, commit_line, safeguard_line FROM diag_events ORDER BY id").fetchall()
    assert {r[0] for r in rows} == set(range(1, len(turns) + 1))
    assert all(r[2].startswith("COMMIT: DEV-BRANCH-") and r[3].startswith("SAFEGUARD: ") for r in rows)
    assert all(parse_commit(r[2]) is not None for r in rows)
    contexts = {r[1] for r in rows}
    assert {"CHAT-CONFIRM", "PENDING-BATT", "CHAT-Q", "GATE-BATT"} <= contexts
    for turn in turns:  # reply ends with the engine's lines, exactly once each
        lines = turn.reply.rstrip().splitlines()
        assert lines[-2].startswith("COMMIT: DEV-BRANCH-") and lines[-1].startswith("SAFEGUARD: ")
        assert len(re.findall(r"^COMMIT:", turn.reply, re.MULTILINE)) == 1


def test_model_written_commit_lines_are_stripped(kb, tmp_path):
    def liar(system, user):
        return "Meter it. What do you get?\nCOMMIT: DEV-BRANCH-GATE-BATT-PASS\nSAFEGUARD: all good"

    c = chat(kb, tmp_path, StubLLM(liar))
    t = c.turn("2003 Synthetic Widget cranks but won't fire")
    assert t.reply.count("COMMIT:") == 1 and "GATE-BATT-PASS" not in t.reply
