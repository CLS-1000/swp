"""Deterministic gate engine (G8). The LLM never decides a branch, supplies a spec, or advances a gate."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from spw import KB_PATH
from spw.gates.pack import TERMINAL_RE, Pack
from spw.specs import Resolution, SpecValue, Vehicle, resolve

COND_LABEL = {
    "KEY_OFF": "key off",
    "KOEO": "key on, engine off",
    "CRANKING": "cranking",
    "RUNNING": "running",
    "UNPLUGGED": "unplugged",
}

PREFIX = "DEV-BRANCH-"
COMMIT_RE = re.compile(
    r"^COMMIT: DEV-BRANCH-(?:(?P<intake>INTAKE)|GATE-(?P<gate>[A-Za-z0-9_]+)-(?P<out>PASS|FAIL|LOW|HIGH|YES|NO)"
    r"|(?P<halt>HALT-NO-TELEMETRY)|RISK-ACK-(?P<ack>[A-Za-z0-9_]+)|PENDING-(?P<pend>[A-Za-z0-9_]+)"
    r"|TERMINAL-(?P<term>[A-Z0-9_]+))$"
)


def commit_token(kind: str, gate: str = "", outcome: str = "") -> str:
    """kind: INTAKE | GATE | HALT | RISK-ACK | PENDING | TERMINAL."""
    if kind == "INTAKE":
        return PREFIX + "INTAKE"
    if kind == "GATE":
        return f"{PREFIX}GATE-{gate}-{outcome}"
    if kind == "HALT":
        return PREFIX + "HALT-NO-TELEMETRY"
    if kind in ("RISK-ACK", "PENDING", "TERMINAL"):
        return f"{PREFIX}{kind}-{gate}"
    raise ValueError(kind)


def commit_line(token: str) -> str:
    return f"COMMIT: {token}"


def parse_commit(line: str) -> tuple[str, str, str] | None:
    """Inverse of commit_token -> (kind, gate_or_code, outcome)."""
    m = COMMIT_RE.match(line.strip())
    if not m:
        return None
    if m["intake"]:
        return ("INTAKE", "", "")
    if m["gate"]:
        return ("GATE", m["gate"], m["out"])
    if m["halt"]:
        return ("HALT", "", "")
    if m["ack"]:
        return ("RISK-ACK", m["ack"], "")
    if m["pend"]:
        return ("PENDING", m["pend"], "")
    return ("TERMINAL", m["term"], "")


@dataclass
class Reading:
    value: float | bool
    unit: str | None = None
    state: str | None = None
    source: str = "regex"


@dataclass
class Event:
    context: str
    commit: str  # a token, e.g. DEV-BRANCH-GATE-BATT-PASS
    safeguard: str
    detail: str = ""


@dataclass
class Status:
    kind: str  # INTAKE | NEED_DTCS | NEED_ACK | NEED_READING | NEED_SPEC | NEED_CHOICE | TERMINAL
    commit: str
    safeguard: str
    gate: dict | None = None
    resolution: Resolution | None = None
    terminal: str = ""
    text: str = ""  # deterministic message for the operator
    question: str = ""  # the single thing being asked


@dataclass
class Session:
    pack: Pack
    vehicle: Vehicle = field(default_factory=Vehicle)
    symptom: str = ""
    intake_done: bool = False
    current: str = ""
    readings: dict[str, Reading] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)
    pending: dict[str, Reading] = field(default_factory=dict)
    acked: set[str] = field(default_factory=set)
    dtcs: list[str] = field(default_factory=list)
    dtcs_captured: bool = False
    spec_choice: dict[str, int] = field(default_factory=dict)
    operator_specs: dict[str, SpecValue] = field(default_factory=dict)
    ledger: list[Event] = field(default_factory=list)


def _bound(gate: dict, spec: SpecValue) -> float | None:
    if spec.value is not None:
        return spec.value
    return spec.vmin if gate["op"] in (">=", ">") else spec.vmax


def spec_fits(gate: dict, spec: SpecValue) -> bool:
    if gate["type"] == "window":
        return spec.vmin is not None and spec.vmax is not None
    if gate["type"] == "threshold":
        return _bound(gate, spec) is not None
    return True


def evaluate(gate: dict, spec: SpecValue | None, value: float | bool) -> str:
    kind = gate["type"]
    if kind == "boolean":
        return "YES" if value else "NO"
    if spec is None or not spec_fits(gate, spec):
        raise ValueError("no usable spec")
    if kind == "window":
        return "LOW" if value < spec.vmin else "HIGH" if value > spec.vmax else "PASS"
    limit = _bound(gate, spec)
    ok = {">=": value >= limit, ">": value > limit, "<=": value <= limit, "<": value < limit}[gate["op"]]
    return "PASS" if ok else "FAIL"


def check_reading(gate: dict, reading: Reading) -> str | None:
    """Return an error message if this reading can't belong to this gate."""
    if gate["type"] == "boolean":
        return None if isinstance(reading.value, bool) else "That step is a yes/no check."
    if isinstance(reading.value, bool):
        return f"That step needs a {gate['unit']} reading, not yes/no."
    if reading.unit and reading.unit.casefold() != gate["unit"].casefold():
        return f"That step needs {gate['unit']}, you gave {reading.unit}."
    if reading.state and reading.state != gate["condition"]:
        return (
            f"That step is measured {COND_LABEL[gate['condition']]}, "
            f"not {COND_LABEL.get(reading.state, reading.state)}."
        )
    return None


class Engine:
    def __init__(self, pack: Pack, kb_path: str | Path = KB_PATH, specs: dict[str, Resolution] | None = None):
        self.pack = pack
        self.kb_path = Path(kb_path)
        self.specs = specs  # fixed resolutions (offline HTML parity / tests); None = resolve from kb
        self.gates = pack.gates

    # ---- sessions -------------------------------------------------------------------------
    def new_session(self, vehicle: Vehicle | None = None, symptom: str = "") -> Session:
        return Session(self.pack, vehicle or Vehicle(), symptom, False, self.pack.start)

    def complete_intake(self, s: Session) -> list[Event]:
        s.intake_done = True
        ev = self._advance(s)
        return ev

    # ---- spec -----------------------------------------------------------------------------
    def resolution(self, s: Session, gate: dict) -> Resolution:
        if gate["type"] == "boolean":
            return Resolution("RESOLVED")
        if self.specs is not None:
            res = self.specs.get(gate["id"], Resolution("UNVERIFIED", note="No cited spec — confirm from your manual"))
            if res.status != "RESOLVED" and gate["id"] in s.operator_specs:
                return Resolution("RESOLVED", s.operator_specs[gate["id"]], [], "operator supplied")
            return res
        return resolve(
            gate, s.vehicle, self.kb_path, choice=s.spec_choice.get(gate["id"]), operator=s.operator_specs.get(gate["id"])
        )

    def _usable(self, s: Session, gate: dict) -> tuple[bool, Resolution]:
        res = self.resolution(s, gate)
        if gate["type"] == "boolean":
            return True, res
        return res.usable and spec_fits(gate, res.spec), res

    # ---- state machine --------------------------------------------------------------------
    def _blocked(self, s: Session, gate: dict) -> str:
        if gate.get("cuts_power") and not s.dtcs_captured:
            return "NEED_DTCS"
        if gate.get("risk") and gate["id"] not in s.acked:
            return "NEED_ACK"
        return ""

    def _advance(self, s: Session) -> list[Event]:
        events: list[Event] = []
        while s.intake_done and s.current in self.gates:
            g = self.gates[s.current]
            if self._blocked(s, g):
                break
            if g["id"] not in s.readings and g["id"] in s.pending:
                s.readings[g["id"]] = s.pending.pop(g["id"])
            if g["id"] not in s.readings:
                break
            ok, res = self._usable(s, g)
            if not ok:
                break
            reading = s.readings[g["id"]]
            outcome = evaluate(g, res.spec, reading.value)
            s.outcomes[g["id"]] = outcome
            if g.get("captures_dtcs") and outcome == "YES":
                s.dtcs_captured = True
            target = g["next"][outcome]
            events.append(
                Event(f"GATE-{g['id']}", commit_token("GATE", g["id"], outcome), g["safeguard"], f"{reading.value}")
            )
            s.current = target
            m = TERMINAL_RE.match(target)
            if m:
                events.append(Event(f"TERMINAL-{m.group(1)}", commit_token("TERMINAL", m.group(1)), "Flow complete."))
                break
        s.ledger.extend(events)
        return events

    def status(self, s: Session) -> Status:
        if not s.intake_done:
            return Status(
                "INTAKE", commit_token("INTAKE"), "Need the symptom and the vehicle (or \"unknown\") to pick a flow.",
                text="What's the symptom, and what vehicle is it? Say \"unknown\" if you don't have the vehicle yet.",
                question="symptom and vehicle",
            )
        m = TERMINAL_RE.match(s.current)
        if m:
            code = m.group(1)
            return Status("TERMINAL", commit_token("TERMINAL", code), "Flow complete.", terminal=code,
                          text=self.pack.terminal_text(code))
        g = self.gates[s.current]
        halt = commit_token("HALT")
        blocked = self._blocked(s, g)
        if blocked == "NEED_DTCS":
            return Status(
                "NEED_DTCS", halt, "Capture every DTC before anything that cuts power.", g,
                text='Hold on: this step cuts power. Scan every module and record all DTCs first, then say "dtcs captured".',
                question="DTCs captured?",
            )
        if blocked == "NEED_ACK":
            return Status(
                "NEED_ACK", halt, "Nothing happens until you ack the risk.", g,
                text=f'> **RISK FLAG:** {g["risk"]}\nReply "ack" to continue.', question="ack?",
            )
        if g["id"] in s.readings:
            ok, res = self._usable(s, g)
            if res.status == "CONFLICT":
                rows = "\n".join(f"| {i + 1} | {c.describe().rsplit(' [', 1)[0]} | {c.cite} |" for i, c in enumerate(res.candidates))
                return Status(
                    "NEED_CHOICE", halt, "Step is UNVERIFIED until you pick a source.", g, res,
                    text=f"Sources disagree on {g['quantity']}. Pick the one you trust.\n\n| # | Spec | Source |\n|---|---|---|\n{rows}",
                    question="which source (1, 2, ...)?",
                )
            if not ok:
                form = "the low and high limits" if g["type"] == "window" else "the limit"
                return Status(
                    "NEED_SPEC", halt, "Step is UNVERIFIED until you give the spec.", g, res,
                    text=f"No cited spec — confirm from your manual. UNVERIFIED. Give me {form} for {g['quantity']} ({g['unit']}) and I'll use your number.",
                    question=f"{g['quantity']} spec from your manual",
                )
        return self._reading_status(s, g, halt)

    def _reading_status(self, s: Session, g: dict, halt: str) -> Status:
        if g["type"] == "boolean":
            return Status("NEED_READING", halt, g["safeguard"], g, text=f"{g['instruction']} (yes/no)", question=g["title"])
        res = self.resolution(s, g)
        cond = COND_LABEL[g["condition"]]
        if res.status == "RESOLVED":
            spec_line = f"Spec ({cond}): {res.spec.describe()}"
        elif res.status == "CONFLICT":
            spec_line = "Spec: sources conflict — UNVERIFIED until you pick one."
        else:
            spec_line = "Spec: UNVERIFIED — no cited spec, confirm from your manual."
        q = f"Need the {g['quantity']}, {cond}."
        return Status("NEED_READING", halt, g["safeguard"], g, res, text=f"{g['instruction']}\n{spec_line}\n{q}", question=q)

    # ---- inputs ---------------------------------------------------------------------------
    def record(self, s: Session, reading: Reading) -> tuple[list[Event], str | None]:
        """Confirmed reading for the current gate."""
        st = self.status(s)
        if st.kind not in ("NEED_READING", "NEED_SPEC", "NEED_CHOICE") or st.gate is None:
            return [], "Not ready for a reading yet."
        err = check_reading(st.gate, reading)
        if err:
            return [], err
        if reading.state is None and st.gate["type"] != "boolean":
            reading = Reading(reading.value, reading.unit, st.gate["condition"], reading.source)
        s.readings[st.gate["id"]] = reading
        return self._advance(s), None

    def hold(self, s: Session, gate_id: str, reading: Reading) -> tuple[list[Event], str | None]:
        """Confirmed reading for a gate we haven't reached (or one blocked on ack). Applied in order later."""
        gate = self.gates.get(gate_id)
        if gate is None or gate_id in s.readings or gate_id in s.outcomes:
            return [], "Nothing to hold that for."
        err = check_reading(gate, reading)
        if err:
            return [], err
        if reading.state is None and gate["type"] != "boolean":
            reading = Reading(reading.value, reading.unit, gate["condition"], reading.source)
        s.pending[gate_id] = reading
        ev = Event(f"PENDING-{gate_id}", commit_token("PENDING", gate_id), "Held until that step comes up.", f"{reading.value}")
        s.ledger.append(ev)
        return [ev], None

    def ack(self, s: Session) -> list[Event]:
        if not s.intake_done or s.current not in self.gates:
            return []
        g = self.gates[s.current]
        if not g.get("risk") or g["id"] in s.acked:
            return []
        s.acked.add(g["id"])
        ev = [Event(f"RISK-ACK-{g['id']}", commit_token("RISK-ACK", g["id"]), g["safeguard"])]
        s.ledger.extend(ev)
        return ev + self._advance(s)

    def mark_dtcs_captured(self, s: Session) -> list[Event]:
        s.dtcs_captured = True
        return self._advance(s)

    def add_dtcs(self, s: Session, codes: list[str]) -> None:
        for c in codes:
            if c not in s.dtcs:
                s.dtcs.append(c)

    def set_operator_spec(self, s: Session, spec: SpecValue) -> list[Event]:
        if s.current in self.gates:
            s.operator_specs[s.current] = spec
        return self._advance(s)

    def choose_spec(self, s: Session, index: int) -> list[Event]:
        if s.current in self.gates:
            s.spec_choice[s.current] = index
        return self._advance(s)


def replay_position(pack: Pack, commits: list[str]) -> str:
    """Rebuild the current position from COMMIT tokens alone (ledger round-trip)."""
    pos = pack.start
    gates = pack.gates
    for token in commits:
        parsed = parse_commit(commit_line(token))
        if parsed is None:
            raise ValueError(f"bad commit {token}")
        kind, gid, out = parsed
        if kind == "GATE":
            if gid != pos:
                raise ValueError(f"commit for {gid} but position is {pos}")
            pos = gates[gid]["next"][out]
    return pos
