"""File readers. pypdf / pandas are ingest-only and imported lazily inside the functions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


class ReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class Section:
    page: int
    section: str
    text: str


@dataclass(frozen=True)
class SpecRow:
    make: str
    model: str
    year_start: int | None
    year_end: int | None
    ecu_family: str
    quantity: str
    value: float | None
    vmin: float | None
    vmax: float | None
    unit: str
    measurement_state: str
    page: int | None


def read_pdf(path: Path) -> list[Section]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ReadError("pypdf not installed: pip install -e .[ingest]") from exc
    try:
        reader = PdfReader(str(path))
        return [Section(i, "", page.extract_text() or "") for i, page in enumerate(reader.pages, start=1)]
    except Exception as exc:  # pypdf raises many types on malformed input
        raise ReadError(f"unreadable pdf: {exc}") from exc


def read_text(path: Path) -> list[Section]:
    """MD/TXT: split on headings. Plain text without headings is one section. Page is always 1."""
    sections: list[Section] = []
    heading = ""
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append(Section(1, heading, body))

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = HEADING_RE.match(line)
        if match and path.suffix.lower() == ".md":
            flush()
            buf = []
            heading = match.group(2)
        else:
            buf.append(line)
    flush()
    return sections


_ALIASES = {
    "make": ("make",),
    "model": ("model",),
    "years": ("years", "year", "yr"),
    "year_start": ("year_start", "start_year", "from"),
    "year_end": ("year_end", "end_year", "to"),
    "ecu_family": ("ecu_family", "ecu", "ecu family", "family"),
    "quantity": ("quantity", "measurement", "parameter", "spec"),
    "value": ("value", "nominal"),
    "min": ("min", "minimum", "vmin"),
    "max": ("max", "maximum", "vmax"),
    "unit": ("unit", "units"),
    "measurement_state": ("measurement_state", "state", "condition"),
    "page": ("page", "pg"),
}


def _num(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _str(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _years(row: dict) -> tuple[int | None, int | None]:
    start, end = _num(row.get("year_start")), _num(row.get("year_end"))
    if start is not None or end is not None:
        return (int(start) if start is not None else None, int(end) if end is not None else None)
    text = _str(row.get("years"))
    match = re.fullmatch(r"(\d{4})\s*[-–]\s*(\d{4})", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    if re.fullmatch(r"\d{4}", text):
        return int(text), int(text)
    return None, None


def read_spec_table(path: Path) -> tuple[list[SpecRow], int]:
    """CSV/XLSX -> normalized spec rows. Returns (rows, skipped_row_count)."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise ReadError("pandas not installed: pip install -e .[ingest]") from exc
    try:
        frame = pd.read_csv(path, dtype=str) if path.suffix.lower() == ".csv" else pd.read_excel(path, dtype=str)
    except Exception as exc:
        raise ReadError(f"unreadable table: {exc}") from exc
    lookup = {str(c).strip().lower(): c for c in frame.columns}
    column = {}
    for canon, names in _ALIASES.items():
        for name in names:
            if name in lookup:
                column[canon] = lookup[name]
                break
    if "quantity" not in column or not ({"value", "min", "max"} & set(column)):
        raise ReadError("table needs a quantity column and value or min/max columns")
    rows: list[SpecRow] = []
    skipped = 0
    for record in frame.to_dict("records"):
        row = {canon: record.get(col) for canon, col in column.items()}
        quantity = _str(row.get("quantity"))
        value, vmin, vmax = _num(row.get("value")), _num(row.get("min")), _num(row.get("max"))
        if not quantity or (value is None and vmin is None and vmax is None):
            skipped += 1
            continue
        y0, y1 = _years(row)
        page = _num(row.get("page"))
        rows.append(
            SpecRow(
                make=_str(row.get("make")),
                model=_str(row.get("model")),
                year_start=y0,
                year_end=y1,
                ecu_family=_str(row.get("ecu_family")),
                quantity=quantity,
                value=value,
                vmin=vmin,
                vmax=vmax,
                unit=_str(row.get("unit")),
                measurement_state=_str(row.get("measurement_state")).upper().replace(" ", "_"),
                page=int(page) if page is not None else None,
            )
        )
    return rows, skipped
