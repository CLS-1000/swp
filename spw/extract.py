"""Regex-first extraction of readings, DTCs and short answers from operator free text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from spw.gates.engine import Reading

UNIT_ALT = (
    r"kohms?|kω|ohms?|ω|millivolts?|mv|volts?|v|milliamps?|ma|amps?|(?-i:A)|kpa|psi|hz|ms"
)
NUM_RE = re.compile(rf"(?<![\w.])(?P<num>\d+(?:\.\d+)?)(?:\s*(?P<unit>{UNIT_ALT})\b)?", re.IGNORECASE)
UNIT_NORM = {
    "v": "V", "volt": "V", "volts": "V", "mv": "mV", "millivolt": "mV", "millivolts": "mV",
    "ohm": "ohm", "ohms": "ohm", "ω": "ohm", "kohm": "kohm", "kohms": "kohm", "kω": "kohm",
    "a": "A", "amp": "A", "amps": "A", "ma": "mA", "milliamp": "mA", "milliamps": "mA",
    "kpa": "kPa", "psi": "psi", "hz": "Hz", "ms": "ms",
}
DTC_RE = re.compile(r"\b[PBCU][0-3][0-9A-F]{3}\b", re.IGNORECASE)
APPROX_RE = re.compile(r"\b(about|around|roughly|approximately|approx|give or take)\b|~|-?ish\b", re.IGNORECASE)
CUE_RE = re.compile(
    r"(?:\b(?:read|reads|reading|got|getting|measured?|is|at|about|around|roughly|approximately|showing|shows|showed)\b|~)"
    r"\W+(?:\w+\W+){0,2}$",
    re.IGNORECASE,
)
STATE_PATTERNS = [
    ("UNPLUGGED", re.compile(r"\b(unplugged|disconnected|connector off|harness off)\b", re.IGNORECASE)),
    ("CRANKING", re.compile(r"\bcrank(?:ing|s|ed)?\b|\bon crank\b", re.IGNORECASE)),
    ("RUNNING", re.compile(r"\b(running|idling|idle|koer|engine on|engine running)\b", re.IGNORECASE)),
    ("KOEO", re.compile(r"\bkoeo\b|\bkey[- ]?on\b|\bignition on\b|\bengine off\b", re.IGNORECASE)),
    ("KEY_OFF", re.compile(r"\bkey[- ]?off\b|\bkoff\b|\bignition off\b", re.IGNORECASE)),
]
YES_RE = re.compile(r"^\s*(y|yes|yeah|yep|yup|correct|right|affirmative|confirmed?|sure|ok|okay)\b[\s.!]*$", re.IGNORECASE)
NO_RE = re.compile(r"^\s*(n|no|nope|nah|negative|wrong|incorrect|not right)\b[\s.!]*$", re.IGNORECASE)
ACK_RE = re.compile(r"^\s*ack[\s.!]*$", re.IGNORECASE)
DTCS_DONE_RE = re.compile(r"\bdtcs?\b.*\b(captured|recorded|saved|logged|written down)\b", re.IGNORECASE)
QUESTION_RE = re.compile(
    r"\?|^\s*(what|why|how|where|which|when|who|can|could|does|do|is|are|should|explain|tell me)\b", re.IGNORECASE
)
NOT_A_READING = re.compile(
    r"\s*(?:min(?:ute)?s?|sec(?:ond)?s?|hours?|hrs?|days?|weeks?|months?|years?|miles?|km|times?|cylinders?|cyl|rpm|mph|"
    r"inch(?:es)?|ft|feet|degrees?|percent|%|°)(?![a-z])",
    re.IGNORECASE,
)
CLAUSE_SPLIT = re.compile(r"[;,]|\.\s")


@dataclass
class Extraction:
    readings: list[Reading] = field(default_factory=list)  # numeric, value float
    clauses: list[str] = field(default_factory=list)  # clause text for each reading (gate matching)
    dtcs: list[str] = field(default_factory=list)
    yes_no: bool | None = None
    yes_no_src: str = "regex"
    approx: bool = False

    @property
    def empty(self) -> bool:
        return not (self.readings or self.dtcs or self.yes_no is not None)


def find_state(text: str) -> str | None:
    for state, pattern in STATE_PATTERNS:
        if pattern.search(text):
            return state
    return None


def is_yes(text: str) -> bool:
    return bool(YES_RE.match(text))


def is_no(text: str) -> bool:
    return bool(NO_RE.match(text))


def is_ack(text: str) -> bool:
    return bool(ACK_RE.match(text))


def says_dtcs_captured(text: str) -> bool:
    return bool(DTCS_DONE_RE.search(text))


NUDGE_RE = re.compile(r"^\s*(what now|what next|next|now what|continue|go on|and then|ready|ok go)\W*$", re.IGNORECASE)


def is_question(text: str) -> bool:
    return bool(QUESTION_RE.search(text)) and not NUDGE_RE.match(text)


def extract_dtcs(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(0).upper() for m in DTC_RE.finditer(text)))


def extract(text: str) -> Extraction:
    ex = Extraction(dtcs=extract_dtcs(text))
    ex.approx = bool(APPROX_RE.search(text))
    if is_yes(text):
        ex.yes_no = True
    elif is_no(text):
        ex.yes_no = False
    short = len(text.split()) <= 8
    scrubbed = DTC_RE.sub(" ", text)
    for clause in CLAUSE_SPLIT.split(scrubbed):
        for m in NUM_RE.finditer(clause):
            unit = UNIT_NORM.get((m.group("unit") or "").lower()) if m.group("unit") else None
            value = float(m.group("num"))
            if unit is None:
                if clause[m.end() : m.end() + 1].isalpha():
                    continue  # '528i', '3rd'
                if NOT_A_READING.match(clause[m.end() :]):
                    continue  # '20 minutes', '3 times'
                if value == int(value) and 1950 <= value <= 2035:
                    continue  # a model year
                if not (short or CUE_RE.search(clause[max(0, m.start() - 40) : m.start()])):
                    continue
            ex.readings.append(Reading(value, unit, find_state(clause), "regex"))
            ex.clauses.append(clause)
    return ex


# ---- vehicle intake ---------------------------------------------------------------------------
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
STOP = {
    "and", "with", "that", "which", "it", "its", "it's", "cranks", "cranking", "won't", "wont", "will", "not", "no",
    "but", "the", "a", "an", "is", "has", "have", "had", "when", "after", "since", "while", "keeps", "dies",
    "stalls", "stalled", "starts", "start", "my", "our", "customer", "came", "in", "on", "for", "i", "we",
}
UNKNOWN_RE = re.compile(r"\b(unknown|don'?t know|not sure|no idea|n/?a)\b", re.IGNORECASE)


def parse_vehicle(text: str) -> tuple[str, str, int | None] | None:
    """Pull '2008 BMW 528i' style intake out of free text. Returns (make, model, year) or None."""
    years = YEAR_RE.search(text)
    if not years:
        return None
    if re.match(r"\s*(ohms?|volts?|v|kpa|psi|hz|ms|rpm|mv)\b", text[years.end() :], re.IGNORECASE):
        return None  # '2000 ohms' is a reading, not a model year
    year = int(years.group(1))
    after = re.findall(r"[A-Za-z0-9-]+", text[years.end() :])
    before = re.findall(r"[A-Za-z0-9-]+", text[: years.start()])
    words: list[str] = []
    for w in after:
        if w.lower() in STOP:
            break
        words.append(w)
        if len(words) == 3:
            break
    if not words:
        for w in reversed(before):
            if w.lower() in STOP:
                break
            words.insert(0, w)
            if len(words) == 3:
                break
    if not words:
        return None
    return words[0], " ".join(words[1:]), year
