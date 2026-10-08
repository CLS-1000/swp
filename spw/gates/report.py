from __future__ import annotations

from pathlib import Path

from spw import KB_PATH, PACKS_DIR
from spw.gates.pack import load_packs
from spw.specs import Vehicle, resolve


def unverified_report(packs_dir: str | Path = PACKS_DIR, kb_path: str | Path = KB_PATH) -> list[dict]:
    """Per pack: numeric gates by resolution status against the knowledge base (vehicle-agnostic rows only)."""
    out = []
    for pack in load_packs(packs_dir):
        counts = {"RESOLVED": 0, "UNVERIFIED": 0, "CONFLICT": 0}
        unresolved = []
        for gate in pack.gates.values():
            if gate["type"] == "boolean":
                continue
            status = resolve(gate, Vehicle(), kb_path).status
            counts[status] += 1
            if status != "RESOLVED":
                unresolved.append(f"{gate['id']} ({gate.get('spec_ref', 'no spec_ref')})")
        out.append({"pack": pack.id, "numeric_gates": sum(counts.values()), **counts, "needs": unresolved})
    return out
