"""G8: Python/JS engine parity."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from spw.gates.pack import check_pack, load_pack
from spw.gates.simulate import simulate

REPO = Path(__file__).resolve().parent.parent
PACK = load_pack(REPO / "tests" / "fixtures" / "pack_synthetic.json")

SPECS = {
    "BATT": {"value": 3.33, "unit": "V"},
    "ECUPWR": {"vmin": 1.11, "vmax": 2.22, "unit": "V"},
    "WIRE": {"value": 4.44, "unit": "V"},
    "MYSTERY": None,
}

HAPPY = [
    {"intake": 1}, {"read": True}, {"read": 5.0}, {"read": 1.5}, {"read": 4.0},
    {"read": 0.5}, {"spec": {"value": 1.0, "unit": "V"}}, {"read": 0.5}, {"dtcs_captured": 1},
    {"read": False}, {"ack": 1}, {"read": True},
]

CASES = {
    "happy": HAPPY,
    "battery_fail": [{"intake": 1}, {"read": True}, {"read": 1.0}, {"read": 9.0}],
    "window_low": [{"intake": 1}, {"read": True}, {"read": 3.33}, {"read": 1.10}],
    "window_high": [{"intake": 1}, {"read": True}, {"read": 3.33}, {"read": 2.23}],
    "bad_state_unit": [{"intake": 1}, {"read": True}, {"read": 5.0, "state": "KOEO"}, {"read": 5.0, "unit": "ohm"}, {"read": True}],
    "before_intake": [{"read": True}, {"ack": 1}, {"intake": 1}],
    "pending_in_order": [{"intake": 1}, {"hold": ["ECUPWR", 1.5], "state": "KOEO"}, {"read": True}, {"read": 5.0}, {"read": 4.0}],
    "pending_wrong_state": [{"intake": 1}, {"hold": ["ECUPWR", 1.5], "state": "RUNNING"}, {"hold": ["NOPE", 1.0]}],
    "spec_gate_unverified": [
        {"intake": 1}, {"read": True}, {"read": 5.0}, {"read": 1.5}, {"read": 4.0}, {"read": 7.0}, {"spec": {"value": 6.0}},
    ],
    "operator_spec_window_misfit": [
        {"intake": 1}, {"read": True}, {"read": 5.0}, {"read": 1.5}, {"read": 4.0}, {"read": 7.0}, {"spec": {"vmin": 1.0}}, {"spec": {"value": 9.0}},
    ],
    "no_double_ack": [{"intake": 1}, {"ack": 1}, {"read": False}],
}

# a second pack whose first gate cuts power and a second with a risk, so halts are reachable at start
HALT_PACK = copy.deepcopy(json.loads((REPO / "tests/fixtures/pack_synthetic.json").read_text()))
HALT_PACK["id"] = "halts"
HALT_PACK["start"] = "GND"
HALT_PACK["gates"] = [g for g in HALT_PACK["gates"] if g["id"] in ("GND", "PROBE")]
HALT_PACK["terminals"] = {"GROUND_BAD": "x", "COMMS_OK": "y", "COMMS_FAULT": "z"}
HALT_SCRIPTS = {
    "halts": [{"intake": 1}, {"read": False}, {"dtcs_captured": 1}, {"read": False}, {"read": True}, {"ack": 1}, {"ack": 1}, {"hold": ["PROBE", True]}],
    "halts_pending_then_ack": [{"intake": 1}, {"hold": ["PROBE", False]}, {"dtcs_captured": 1}, {"read": False}, {"ack": 1}],
}


def _jobs():
    for name, script in CASES.items():
        yield name, PACK.data, SPECS, script
    for name, script in HALT_SCRIPTS.items():
        yield name, HALT_PACK, {}, script


def test_halt_pack_is_sound():
    assert check_pack(HALT_PACK) == []


def _py(pack_data, specs, script):
    from spw.gates.pack import Pack

    return simulate(Pack(pack_data), specs, script)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_python_and_js_agree():
    jobs = [{"name": n, "pack": p, "specs": s, "script": sc} for n, p, s, sc in _jobs()]
    runner = (
        "const G=require(process.argv[1]);const jobs=JSON.parse(require('fs').readFileSync(0,'utf8'));"
        "const out={};jobs.forEach(j=>{out[j.name]=G.simulate(j.pack,j.specs,j.script)});console.log(JSON.stringify(out));"
    )
    proc = subprocess.run(
        ["node", "-e", runner, str(REPO / "web" / "gates.js")],
        input=json.dumps(jobs), capture_output=True, text=True, check=True,
    )
    js = json.loads(proc.stdout)
    for job in jobs:
        py = _py(job["pack"], job["specs"], job["script"])
        assert js[job["name"]] == py, job["name"]


def test_python_side_sanity():
    out = _py(PACK.data, SPECS, HAPPY)
    assert out[-1]["events"][-1] == "DEV-BRANCH-TERMINAL-COMMS_OK"
    assert out[0]["events"] == [] and out[0]["kind"] == "NEED_READING"
