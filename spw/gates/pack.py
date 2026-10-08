"""Gate packs: JSON files that define a diagnostic flow. Loading validates integrity (G7)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spw import PACKS_DIR

STATES = ("KEY_OFF", "KOEO", "CRANKING", "RUNNING", "UNPLUGGED")
GATE_TYPES = {"threshold": ("PASS", "FAIL"), "window": ("LOW", "PASS", "HIGH"), "boolean": ("YES", "NO")}
OPS = (">=", ">", "<=", "<")
ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
TERMINAL_RE = re.compile(r"^TERMINAL-([A-Z0-9_]+)$")
CITE_RE = re.compile(r"^\S.* p\.\d+$")
TEXT_FIELDS = ("title", "instruction", "safeguard", "risk")


class PackError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Pack:
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def ecu_family(self) -> str:
        return self.data.get("ecu_family", "")

    @property
    def start(self) -> str:
        return self.data["start"]

    @property
    def gates(self) -> dict[str, dict[str, Any]]:
        return {g["id"]: g for g in self.data["gates"]}

    @property
    def order(self) -> list[str]:
        return [g["id"] for g in self.data["gates"]]

    def terminal_text(self, code: str) -> str:
        return self.data.get("terminals", {}).get(code, "")

    def matches_symptom(self, text: str) -> bool:
        return any(re.search(p, text, re.IGNORECASE) for p in self.data.get("symptoms", []))


def check_pack(data: dict[str, Any]) -> list[str]:
    """Return a list of integrity problems (empty = sound)."""
    problems: list[str] = []
    if not isinstance(data.get("id"), str) or not ID_RE.match(data.get("id", "")):
        problems.append("pack id missing or invalid")
    gates = data.get("gates")
    if not isinstance(gates, list) or not gates:
        return problems + ["pack has no gates"]
    ids = [g.get("id") for g in gates]
    for gid in ids:
        if not isinstance(gid, str) or not ID_RE.match(gid):
            problems.append(f"invalid gate id {gid!r} (letters, digits, underscore only)")
    if len(set(ids)) != len(ids):
        problems.append("duplicate gate ids")
    if data.get("start") not in ids:
        problems.append("start gate does not exist")
    terminals = data.get("terminals", {})
    for code in terminals:
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            problems.append(f"terminal code {code!r} must be A-Z, 0-9, underscore")
    for pattern in data.get("symptoms", []):
        try:
            re.compile(pattern)
        except re.error:
            problems.append(f"bad symptom regex {pattern!r}")
    for g in gates:
        gid = g.get("id", "?")
        gtype = g.get("type")
        if gtype not in GATE_TYPES:
            problems.append(f"{gid}: unknown type {gtype!r}")
            continue
        if gtype != "boolean":
            if g.get("condition") not in STATES:
                problems.append(f"{gid}: condition must be one of {STATES}")
            if not g.get("unit"):
                problems.append(f"{gid}: numeric gate needs a unit")
            if not g.get("spec_ref") and not g.get("literal"):
                problems.append(f"{gid}: numeric gate needs spec_ref or a cited literal")
            if gtype == "threshold" and g.get("op") not in OPS:
                problems.append(f"{gid}: threshold needs op in {OPS}")
        elif g.get("condition") not in (None, *STATES):
            problems.append(f"{gid}: bad condition")
        literal = g.get("literal")
        if literal is not None:
            if not CITE_RE.match(str(literal.get("cite", ""))):
                problems.append(f"{gid}: literal needs a cite like 'file.pdf p.12'")
            if gtype == "window" and not ({"min", "max"} <= set(literal)):
                problems.append(f"{gid}: window literal needs min and max")
            if gtype == "threshold" and "value" not in literal:
                problems.append(f"{gid}: threshold literal needs value")
        for field in TEXT_FIELDS:
            text = g.get(field) or ""
            if re.search(r"\d", text):
                problems.append(f"{gid}: digits in {field} (numbers must come from specs, not pack prose)")
        if not g.get("instruction"):
            problems.append(f"{gid}: missing instruction")
        if not g.get("safeguard"):
            problems.append(f"{gid}: missing safeguard")
        nxt = g.get("next", {})
        expected = set(GATE_TYPES[gtype])
        if set(nxt) != expected:
            problems.append(f"{gid}: next must map exactly {sorted(expected)}")
        for outcome, target in nxt.items():
            if target in ids:
                continue
            m = TERMINAL_RE.match(str(target))
            if not m:
                problems.append(f"{gid}: {outcome} -> {target!r} is not a gate or TERMINAL-<code>")
            elif m.group(1) not in terminals:
                problems.append(f"{gid}: {outcome} -> undefined terminal {m.group(1)}")
    for code, text in terminals.items():
        if re.search(r"\d", text):
            problems.append(f"terminal {code}: digits in text")
    if not problems:
        seen: set[str] = set()
        stack = [data["start"]]
        by_id = {g["id"]: g for g in gates}
        while stack:
            gid = stack.pop()
            if gid in seen:
                continue
            seen.add(gid)
            stack += [t for t in by_id[gid]["next"].values() if t in by_id]
        for gid in ids:
            if gid not in seen:
                problems.append(f"{gid}: unreachable from start")
    return problems


def load_pack(path: str | Path) -> Pack:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = check_pack(data)
    if problems:
        raise PackError(problems)
    return Pack(data)


def load_packs(directory: str | Path = PACKS_DIR) -> list[Pack]:
    d = Path(directory)
    return [load_pack(p) for p in sorted(d.glob("*.json"))] if d.is_dir() else []
