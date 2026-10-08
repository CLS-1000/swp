"""Number and citation audit: a reply may only contain numbers from engine output, retrieved
chunks, or things the operator said."""

from __future__ import annotations

import re

NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
LIST_MARKER = re.compile(r"(?m)^\s*(?:\d+[.)]\s+|[-*]\s+)")
CITE = re.compile(r"\[([^\[\]]+? p\.\d+)\]")
COMMIT_LINES = re.compile(r"(?m)^\s*(?:COMMIT|SAFEGUARD):.*$")
WORD_NUMS = (
    "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    "seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred"
)
WORD_WITH_UNIT = re.compile(
    rf"\b(?P<w>{WORD_NUMS})(?:[- ](?:{WORD_NUMS}))?\s+(?:volts?|ohms?|amps?|milliamps?|millivolts?|kpa|psi|hz|ft-?lbs?|nm)\b",
    re.IGNORECASE,
)


def _norm(token: str) -> float:
    return round(float(token), 6)


def numbers_in(text: str) -> set[float]:
    return {_norm(t) for t in NUM.findall(text)}


def strip_markers(text: str) -> str:
    return LIST_MARKER.sub("", COMMIT_LINES.sub("", text))


def audit_numbers(reply: str, allowed_texts: list[str]) -> list[str]:
    """Return offending numbers / number-words / citations in `reply` (empty = clean)."""
    allowed_blob = "\n".join(allowed_texts)
    allowed = numbers_in(allowed_blob)
    body = strip_markers(reply)
    bad: list[str] = []
    for token in NUM.findall(body):
        if _norm(token) not in allowed:
            bad.append(token)
    low_allowed = allowed_blob.lower()
    for m in WORD_WITH_UNIT.finditer(body):
        if m.group(0).lower() not in low_allowed:
            bad.append(m.group(0))
    cites = {c.strip() for c in CITE.findall(allowed_blob)}
    for cite in CITE.findall(body):
        if cite.strip() not in cites:
            bad.append(f"[{cite}]")
    return bad
