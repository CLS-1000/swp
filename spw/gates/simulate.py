"""Scripted engine runs, used for Python/JS parity checks and fixtures."""

from __future__ import annotations

from spw.gates.engine import Engine, Reading
from spw.gates.pack import Pack
from spw.specs import Resolution, SpecValue


def _spec(d: dict | None) -> Resolution:
    if d is None:
        return Resolution("UNVERIFIED")
    sv = SpecValue(d.get("value"), d.get("vmin"), d.get("vmax"), d.get("unit", ""), d.get("cite", "fixture"), "table")
    return Resolution("RESOLVED", sv, [sv])


def simulate(pack: Pack, specs: dict[str, dict | None], script: list[dict]) -> list[dict]:
    engine = Engine(pack, specs={k: _spec(v) for k, v in specs.items()})
    s = engine.new_session()
    out: list[dict] = []
    for step in script:
        events, error = [], None
        if "intake" in step:
            events = engine.complete_intake(s)
        elif "read" in step:
            events, error = engine.record(s, Reading(step["read"], step.get("unit"), step.get("state")))
        elif "hold" in step:
            events, error = engine.hold(s, step["hold"][0], Reading(step["hold"][1], step.get("unit"), step.get("state")))
        elif "ack" in step:
            events = engine.ack(s)
        elif "dtcs_captured" in step:
            events = engine.mark_dtcs_captured(s)
        elif "spec" in step:
            d = step["spec"]
            events = engine.set_operator_spec(s, SpecValue(d.get("value"), d.get("vmin"), d.get("vmax"), d.get("unit", ""), "operator", "operator"))
        out.append(
            {"events": [e.commit for e in events], "error": error is not None, "kind": engine.status(s).kind, "current": s.current}
        )
    return out
