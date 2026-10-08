"""Spec resolution: specs table -> cited literal in pack -> UNVERIFIED. Never invents a value."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from spw import KB_PATH
from spw.lookup import model_matches


@dataclass(frozen=True)
class Vehicle:
    make: str = ""
    model: str = ""
    year: int | None = None

    @property
    def known(self) -> bool:
        return bool(self.make or self.model or self.year)

    def label(self) -> str:
        return " ".join(str(p) for p in (self.year, self.make, self.model) if p) or "unknown"


@dataclass(frozen=True)
class SpecValue:
    value: float | None
    vmin: float | None
    vmax: float | None
    unit: str
    cite: str
    source: str  # table | pack | operator

    def key(self) -> tuple:
        r = lambda x: None if x is None else round(x, 6)
        return (r(self.value), r(self.vmin), r(self.vmax), self.unit.casefold())

    def describe(self) -> str:
        if self.vmin is not None and self.vmax is not None:
            body = f"{self.vmin:g}–{self.vmax:g} {self.unit}"
        elif self.value is not None:
            body = f"{self.value:g} {self.unit}"
        elif self.vmin is not None:
            body = f"≥ {self.vmin:g} {self.unit}"
        else:
            body = f"≤ {self.vmax:g} {self.unit}"
        return f"{body.strip()} [{self.cite}]"


@dataclass
class Resolution:
    status: str  # RESOLVED | UNVERIFIED | CONFLICT
    spec: SpecValue | None = None
    candidates: list[SpecValue] = field(default_factory=list)
    note: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "RESOLVED" and self.spec is not None


def _row_matches(row: sqlite3.Row, vehicle: Vehicle) -> bool:
    if row["make"] and (not vehicle.make or row["make"].casefold() != vehicle.make.casefold()):
        return False
    if row["model"] and (not vehicle.model or not model_matches(vehicle.model, row["model"])):
        return False
    if row["year_start"] is not None or row["year_end"] is not None:
        if vehicle.year is None:
            return False
        if row["year_start"] is not None and vehicle.year < row["year_start"]:
            return False
        if row["year_end"] is not None and vehicle.year > row["year_end"]:
            return False
    return True


def _table_candidates(spec_ref: str, unit: str, vehicle: Vehicle, kb_path: Path) -> tuple[list[SpecValue], int]:
    if not Path(kb_path).exists():
        return [], 0
    conn = sqlite3.connect(kb_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM specs WHERE spec_key=? ORDER BY id", (spec_ref,)).fetchall()
    except sqlite3.OperationalError:  # kb never ingested
        return [], 0
    finally:
        conn.close()
    out: list[SpecValue] = []
    ignored = 0
    for row in rows:
        if not _row_matches(row, vehicle) or (unit and row["unit"] and row["unit"].casefold() != unit.casefold()):
            ignored += 1
            continue
        cite = f"{row['source_file']} p.{row['page']}" if row["page"] is not None else row["source_file"]
        out.append(SpecValue(row["value"], row["vmin"], row["vmax"], row["unit"] or unit, cite, "table"))
    return out, ignored


def _merge(cands: list[SpecValue]) -> list[SpecValue]:
    """Same numbers from several files agree; merge their cites. Differing numbers stay separate."""
    merged: dict[tuple, SpecValue] = {}
    for c in cands:
        k = c.key()
        if k in merged:
            prev = merged[k]
            merged[k] = SpecValue(prev.value, prev.vmin, prev.vmax, prev.unit, f"{prev.cite}; {c.cite}", prev.source)
        else:
            merged[k] = c
    return list(merged.values())


def resolve(
    gate: dict,
    vehicle: Vehicle | None = None,
    kb_path: str | Path = KB_PATH,
    choice: int | None = None,
    operator: SpecValue | None = None,
) -> Resolution:
    """Resolve the spec for a numeric gate. `choice` indexes the conflict candidates; `operator` is
    a value the operator typed in (the only way an UNVERIFIED gate becomes usable)."""
    vehicle = vehicle or Vehicle()
    unit = gate.get("unit", "")
    ref = gate.get("spec_ref")
    cands: list[SpecValue] = []
    ignored = 0
    if ref:
        cands, ignored = _table_candidates(ref, unit, vehicle, Path(kb_path))
        cands = _merge(cands)
    if not cands and gate.get("literal"):
        lit = gate["literal"]
        cands = [SpecValue(lit.get("value"), lit.get("min"), lit.get("max"), lit.get("unit", unit), lit["cite"], "pack")]
    if len(cands) == 1:
        return Resolution("RESOLVED", cands[0], cands)
    if len(cands) > 1:
        if choice is not None and 0 <= choice < len(cands):
            return Resolution("RESOLVED", cands[choice], cands, "operator chose")
        if operator is not None:
            return Resolution("RESOLVED", operator, cands, "operator supplied")
        return Resolution("CONFLICT", None, cands, "sources disagree")
    if operator is not None:
        return Resolution("RESOLVED", operator, [], "operator supplied")
    note = "No cited spec — confirm from your manual"
    if ignored and not vehicle.known:
        note += " (vehicle unknown, so vehicle-specific rows were not used)"
    return Resolution("UNVERIFIED", None, [], note)
