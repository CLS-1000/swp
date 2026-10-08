"""Conversational layer. The engine decides; retrieval grounds; the LLM only phrases and (second) extracts."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from spw import DIAG_DB_PATH, KB_PATH
from spw.audit import WORD_NUMS, audit_numbers
from spw.diagdb import append_events
from spw.extract import (
    DTC_RE,
    UNKNOWN_RE,
    YEAR_RE,
    Extraction,
    extract,
    extract_dtcs,
    find_state,
    is_ack,
    is_no,
    is_question,
    is_yes,
    parse_vehicle,
    says_dtcs_captured,
)
from spw.gates.engine import (
    COND_LABEL,
    Engine,
    Event,
    Reading,
    Session,
    Status,
    commit_line,
    commit_token,
    spec_text,
)
from spw.gates.pack import Pack
from spw.ingest.store import Chunk, retrieve
from spw.llm import LLM, LLMError
from spw.specs import SpecValue, Vehicle

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "diag_system.md").read_text(encoding="utf-8")
EXPLAIN_RULES = (
    "Write the reply body only: 2-4 short plain sentences explaining the next step, then ask exactly the one "
    "question given. Cite an excerpt as [file p.N] only if you use it. Do not state any number that is not in "
    "ENGINE or EXCERPTS or said by the operator. Do not decide outcomes. Do not write COMMIT or SAFEGUARD lines."
)
WORD = re.compile(r"[a-z0-9]+")
OUTCOME_WORDS = re.compile(r"\b(pass(?:ed|es)?|fail(?:ed|s|ure)?|in[- ]spec|out of spec|too (?:low|high))\b", re.IGNORECASE)
NUMBERISH = re.compile(rf"\d|\b(?:{WORD_NUMS})\b", re.IGNORECASE)
OUTCOME_LABEL = {"PASS": "pass", "FAIL": "fail", "LOW": "low", "HIGH": "high", "YES": "yes", "NO": "no"}
NONE_YET = "Nothing is logged until you confirm."


@dataclass
class Confirm:
    """Things extracted from the operator's words that wait for a yes before they count."""

    readings: list[tuple[str, Reading]] = field(default_factory=list)  # (target gate id, reading)
    dtcs: list[str] = field(default_factory=list)
    spec: SpecValue | None = None
    booleans: list[tuple[str, bool]] = field(default_factory=list)

    def describe(self, engine: Engine) -> str:
        bits = []
        for gid, r in self.readings:
            g = engine.gates[gid]
            val = f"{r.value:g}" if not isinstance(r.value, bool) else ("yes" if r.value else "no")
            cond = COND_LABEL.get(r.state or g.get("condition", ""), "")
            bits.append(f"{val} {r.unit or g.get('unit', '')}, {cond} (for the {g['title'].lower()} step)".replace("  ", " "))
        if self.dtcs:
            bits.append("DTCs " + ", ".join(self.dtcs))
        for gid, b in self.booleans:
            bits.append(f"{'yes' if b else 'no'} on \"{engine.gates[gid]['title'].lower()}\"")
        if self.spec:
            bits.append("your spec " + self.spec.describe().rsplit(" [", 1)[0])
        return "Logging " + " and ".join(bits) + " — right?"


@dataclass
class Turn:
    reply: str
    commit: str
    safeguard: str
    context: str
    events: list[Event]
    used_llm: bool = False
    audit_rejected: bool = False
    allowed: list[str] = field(default_factory=list)


class ChatSession:
    def __init__(
        self,
        packs: list[Pack],
        kb_path: str | Path = KB_PATH,
        llm: LLM | None = None,
        diag_db: str | Path | None = DIAG_DB_PATH,
        session_id: str | None = None,
        k: int = 3,
    ):
        self.packs = packs
        self.kb_path = Path(kb_path)
        self.llm = llm
        self.diag_db = diag_db
        self.sid = session_id or uuid.uuid4().hex[:12]
        self.k = k
        self.engine: Engine | None = None
        self.state: Session | None = None
        self.vehicle = Vehicle()
        self.symptom = ""
        self.turn_no = 0
        self.awaiting: Confirm | str | None = None  # Confirm | "vehicle" | None
        self.vehicle_asked = False
        self.user_texts: list[str] = []
        self.last: Turn | None = None

    # ---- public ----------------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return "llm" if self.llm is not None else "offline"

    def turn(self, text: str) -> Turn:
        text = text.strip()
        self.turn_no += 1
        self.user_texts.append(text)
        events: list[Event] = []
        prefix: list[str] = []
        context = ""
        general_q = None

        just_selected = False
        if self.engine is None:
            self._select_pack(text, prefix)
            just_selected = self.engine is not None
        if self.engine is not None:
            context = self._handle(text, events, prefix, first=just_selected)
            if context == "CHAT-Q":
                general_q = text

        result = self._compose(text, events, prefix, context, general_q)
        self.last = result
        if self.diag_db:
            rows = list(events)
            if not rows or rows[-1].commit != result.commit or result.context in ("CHAT-Q", "CHAT-CONFIRM"):
                rows.append(Event(result.context or self._status_context(result.commit), result.commit, result.safeguard))
            append_events(self.diag_db, self.sid, self.turn_no, rows)
        return result

    # ---- intake ----------------------------------------------------------------------------
    def _select_pack(self, text: str, prefix: list[str]) -> None:
        self.symptom = self.symptom or text
        veh = parse_vehicle(text)
        if veh:
            self.vehicle = Vehicle(veh[0], veh[1], veh[2])
        pack = next((p for p in self.packs if p.matches_symptom(text)), None)
        if pack is None:
            return
        self.engine = Engine(pack, kb_path=self.kb_path)
        self.state = self.engine.new_session(self.vehicle, self.symptom)
        self.engine.complete_intake(self.state)
        self.state.ledger.clear()
        prefix.append(f"Got it: {pack.data['title'].lower()}." + (f" Vehicle: {self.vehicle.label()}." if self.vehicle.known else ""))

    # ---- turn handling ---------------------------------------------------------------------
    def _handle(self, text: str, events: list[Event], prefix: list[str], first: bool = False) -> str:
        eng, s = self.engine, self.state
        assert eng is not None and s is not None
        aw = self.awaiting
        if isinstance(aw, str) and aw == "vehicle":
            self.awaiting = None
            self._take_vehicle(text, prefix)
        elif isinstance(aw, Confirm):
            if is_yes(text):
                self.awaiting = None
                self._apply(aw, events, prefix)
                return "CHAT-CONFIRM"
            if is_no(text):
                self.awaiting = None
                prefix.append("Scratched that, nothing logged.")
                return "CHAT-CONFIRM"
            self.awaiting = None
            prefix.append("Dropped the unconfirmed reading.")
        if not first:
            self._take_vehicle(text, prefix, quiet=True)

        st = eng.status(s)
        if st.kind == "NEED_ACK" and is_ack(text):
            events += eng.ack(s)
            return ""
        if st.kind == "NEED_DTCS" and says_dtcs_captured(text):
            events += eng.mark_dtcs_captured(s)
            return ""
        if st.kind == "NEED_CHOICE" and re.fullmatch(r"\s*#?(\d+)\s*", text):
            idx = int(re.fullmatch(r"\s*#?(\d+)\s*", text).group(1)) - 1  # type: ignore[union-attr]
            if 0 <= idx < len(st.resolution.candidates):  # type: ignore[union-attr]
                events += eng.choose_spec(s, idx)
                events.insert(0, Event("CHAT-CONFIRM", commit_token("HALT"), f"Operator picked source {idx + 1}.", f"choice={idx + 1}"))
                return "CHAT-CONFIRM"

        ex = extract(text)
        gate = eng.gates.get(s.current)
        if ex.yes_no is not None and gate is not None and gate["type"] == "boolean" and st.kind == "NEED_READING":
            # a direct yes/no to a direct yes/no question is its own confirmation
            ev, err = eng.record(s, Reading(ex.yes_no, None, None, "regex"))
            events += ev
            if err:
                prefix.append(err)
            return ""
        if (
            ex.empty
            and self.llm
            and st.kind in ("NEED_READING", "NEED_SPEC")
            and not is_question(text)
            and not is_ack(text)
            and NUMBERISH.search(YEAR_RE.sub(" ", DTC_RE.sub(" ", text)))
        ):
            ex = self._llm_extract(text, ex)
        confirm = self._plan(ex, st)
        if confirm is not None:
            self.awaiting = confirm
            return "CHAT-CONFIRM"
        if is_question(text) and ex.empty:
            return "CHAT-Q"
        return ""

    def _take_vehicle(self, text: str, prefix: list[str], quiet: bool = False) -> None:
        veh = parse_vehicle(text)
        if veh and not self.vehicle.known:
            self.vehicle = Vehicle(veh[0], veh[1], veh[2])
            if self.state:
                self.state.vehicle = self.vehicle
            if not quiet:
                prefix.append(f"Vehicle: {self.vehicle.label()}.")
        elif not quiet and UNKNOWN_RE.search(text):
            prefix.append("Vehicle noted as unknown.")

    def _llm_extract(self, text: str, ex: Extraction) -> Extraction:
        try:
            data = self.llm.extract(text)  # type: ignore[union-attr]
        except LLMError:
            return ex
        for item in data.get("readings", []) or []:
            try:
                value = float(item["value"])
            except (KeyError, TypeError, ValueError):
                continue
            unit = item.get("unit") if item.get("unit") not in (None, "null", "") else None
            state = item.get("state") if item.get("state") in ("KEY_OFF", "KOEO", "CRANKING", "RUNNING", "UNPLUGGED") else None
            ex.readings.append(Reading(value, unit, state or find_state(text), "llm"))
            ex.clauses.append(text)
        ex.dtcs += [d for d in extract_dtcs(" ".join(map(str, data.get("dtcs", []) or []))) if d not in ex.dtcs]
        if isinstance(data.get("yes_no"), bool):
            ex.yes_no, ex.yes_no_src = data["yes_no"], "llm"
        return ex

    # ---- planning what the operator's words mean --------------------------------------------
    def _score(self, gate: dict, r: Reading, clause: str, current: str) -> float:
        if gate["type"] == "boolean" or isinstance(r.value, bool):
            return -1e9
        if r.unit and r.unit.casefold() != gate["unit"].casefold():
            return -1e9
        if r.state and r.state != gate["condition"]:
            return -1e9
        words = set(WORD.findall(clause.lower()))
        score = 0.0
        if r.state:
            score += 2
        score += sum(1 for w in set(WORD.findall(gate.get("quantity", "").lower())) if w in words)
        if gate["id"] == current:
            score += 0.5
        return score

    def _plan(self, ex: Extraction, st: Status) -> Confirm | None:
        eng, s = self.engine, self.state
        assert eng is not None and s is not None
        if st.kind == "TERMINAL":
            return None
        c = Confirm()
        cur = s.current
        gate = eng.gates.get(cur)
        if ex.yes_no is not None and ex.yes_no_src == "llm" and gate is not None and gate["type"] == "boolean" and st.kind == "NEED_READING":
            c.booleans.append((cur, ex.yes_no))
        if st.kind == "NEED_SPEC" and ex.readings and gate is not None:
            vals = [r.value for r in ex.readings]
            if gate["type"] == "window" and len(vals) >= 2:
                lo, hi = sorted(vals[:2])
                c.spec = SpecValue(None, lo, hi, gate["unit"], "operator", "operator")
            elif gate["type"] == "threshold":
                c.spec = SpecValue(vals[0], None, None, gate["unit"], "operator", "operator")
            if c.spec:
                return c
        unreached = [g for gid, g in eng.gates.items() if gid not in s.outcomes and gid not in s.readings]
        for r, clause in zip(ex.readings, ex.clauses, strict=True):
            scored = sorted(((self._score(g, r, clause, cur), g["id"]) for g in unreached), reverse=True)
            if not scored or scored[0][0] < 0:
                continue
            if len(scored) > 1 and scored[1][0] == scored[0][0] and scored[0][1] != cur:
                continue  # ambiguous: don't guess which step it belongs to
            c.readings.append((scored[0][1], r))
        if ex.dtcs:
            c.dtcs = ex.dtcs
        if c.readings or c.dtcs or c.booleans:
            return c
        return None

    def _apply(self, c: Confirm, events: list[Event], prefix: list[str]) -> None:
        eng, s = self.engine, self.state
        assert eng is not None and s is not None
        if c.dtcs:
            eng.add_dtcs(s, c.dtcs)
            prefix.append("DTCs logged: " + ", ".join(c.dtcs) + ".")
        if c.spec:
            events += eng.set_operator_spec(s, c.spec)
            prefix.append("Using your spec for this step.")
        for gid, b in c.booleans:
            ev, err = eng.record(s, Reading(b, None, None, "llm"))
            events += ev
            if err:
                prefix.append(err)
        for gid, r in c.readings:
            st = eng.status(s)
            if gid == s.current and st.kind in ("NEED_READING", "NEED_SPEC", "NEED_CHOICE"):
                ev, err = eng.record(s, r)
            else:
                ev, err = eng.hold(s, gid, r)
            events += ev
            if err:
                prefix.append(err)

    # ---- composing the reply ----------------------------------------------------------------
    @staticmethod
    def _status_context(commit: str) -> str:
        if commit.endswith("HALT-NO-TELEMETRY"):
            return "HALT"
        return "INTAKE" if commit.endswith("INTAKE") else "TURN"

    def _compose(self, text: str, events: list[Event], prefix: list[str], context: str, general_q: str | None) -> Turn:
        eng, s = self.engine, self.state
        allowed: list[str] = list(self.user_texts)
        if eng is None or s is None:
            return self._compose_no_flow(text, prefix, allowed)

        st = eng.status(s)
        awaiting_confirm = isinstance(self.awaiting, Confirm)
        asked_vehicle = False
        if (
            not awaiting_confirm
            and st.kind in ("NEED_READING", "NEED_SPEC")
            and st.resolution is not None
            and st.resolution.needs_vehicle
            and not self.vehicle.known
            and not self.vehicle_asked
        ):
            self.vehicle_asked = True
            self.awaiting = "vehicle"
            asked_vehicle = True

        # deterministic body parts
        parts = list(prefix)
        for line in self._outcome_lines(events):
            parts.append(line)
            allowed.append(line)
        used_llm = False
        rejected = False
        chunks: list[Chunk] = []
        if general_q:
            chunks = retrieve(general_q, self.k, self.kb_path)
            parts.append(self._answer_general(general_q, chunks, allowed))
            context = "CHAT-Q"
        if awaiting_confirm:
            assert isinstance(self.awaiting, Confirm)
            parts.append(self.awaiting.describe(eng))
            commit, safeguard = st.commit, NONE_YET
            context = "CHAT-CONFIRM"
        elif asked_vehicle:
            parts.append("What vehicle is this? The specs I have for this step are model-specific. Say \"unknown\" if you don't have it.")
            commit, safeguard = st.commit, st.safeguard
        else:
            step_text = st.text
            allowed += [st.text, st.question, st.safeguard]
            if st.resolution is not None:
                allowed += [c.describe() for c in st.resolution.candidates] + ([st.resolution.spec.describe()] if st.resolution.spec else [])
            if st.kind == "NEED_READING" and st.gate is not None:
                chunks = self._gate_chunks(st)
                allowed += [c.text for c in chunks] + [f"[{c.cite}]" for c in chunks]
                # risk and power-cut steps always show the engine's own words, never a paraphrase
                plain = not (st.gate.get("risk") or st.gate.get("cuts_power"))
                llm_body = self._llm_explain(text, st, chunks, allowed) if self.llm and plain else None
                if llm_body is None:
                    if self.llm and plain:
                        rejected = self._last_rejected
                    parts.append(step_text)
                    parts += self._sources(chunks)
                else:
                    used_llm = True
                    parts.append(llm_body)
                    if st.gate["type"] != "boolean":
                        if st.resolution is not None and st.resolution.usable:
                            parts.append(f"Spec: {st.resolution.spec.describe()}")  # type: ignore[union-attr]
                        else:
                            parts.append("Spec: UNVERIFIED — no cited spec, confirm from your manual.")
            else:
                parts.append(step_text)
            commit, safeguard = st.commit, st.safeguard
        if events and not awaiting_confirm and not asked_vehicle:
            commit = events[-1].commit
            safeguard = st.safeguard if st.kind != "TERMINAL" else events[-1].safeguard
        parts_text = "\n\n".join(p for p in parts if p)
        allowed += [e.safeguard for e in events] + [e.commit for e in events] + [commit, safeguard]
        reply = f"{parts_text}\n\n{commit_line(commit)}\nSAFEGUARD: {safeguard}"
        return Turn(reply, commit, safeguard, context, events, used_llm, rejected, allowed)

    def _outcome_lines(self, events: list[Event]) -> list[str]:
        eng, s = self.engine, self.state
        out = []
        for ev in events:
            if not ev.context.startswith("GATE-") or eng is None or s is None:
                continue
            gid = ev.context[5:]
            g, r = eng.gates[gid], s.readings.get(gid)
            outcome = s.outcomes.get(gid, "")
            label = OUTCOME_LABEL.get(outcome, outcome.lower())
            if g["type"] == "window" and outcome == "PASS":
                label = "in-spec"
            line = f"{g['title']}: {label}"
            if r is not None and g["type"] != "boolean":
                res = eng.resolution(s, g)
                if res.usable:
                    line += f" ({r.value:g} {g['unit']} vs {spec_text(g, res.spec)})"  # type: ignore[union-attr]
            out.append(line)
        return out

    def _compose_no_flow(self, text: str, prefix: list[str], allowed: list[str]) -> Turn:
        commit = commit_token("INTAKE")
        safeguard = "Need a symptom I have a flow for before any measurement."
        chunks = retrieve(text, self.k, self.kb_path)
        parts = list(prefix)
        if chunks:
            parts.append(self._answer_general(text, chunks, allowed))
        parts.append("I don't have a flow for that symptom yet. Describe it plainly (for example: cranks but won't fire) and give me the vehicle if you have it.")
        reply = "\n\n".join(parts) + f"\n\n{commit_line(commit)}\nSAFEGUARD: {safeguard}"
        allowed += [safeguard, commit]
        return Turn(reply, commit, safeguard, "CHAT-Q" if chunks else "INTAKE", [], False, False, allowed)

    # ---- grounding helpers -------------------------------------------------------------------
    def _gate_chunks(self, st: Status) -> list[Chunk]:
        """Chunks about this step; ones that mention the vehicle sort first (stable)."""
        g = st.gate or {}
        hits = retrieve(" ".join(filter(None, [g.get("title"), g.get("quantity")])), self.k * 3, self.kb_path)
        terms = [t.lower() for t in (self.vehicle.make, self.vehicle.model) if t]
        hits.sort(key=lambda c: 0 if any(t in c.text.lower() for t in terms) else 1)
        return hits[: self.k]

    def _sources(self, chunks: list[Chunk]) -> list[str]:
        if not chunks:
            return []
        lines = ["Sources:"]
        for c in chunks:
            snippet = " ".join(c.text.split())[:200]
            lines.append(f"- [{c.cite}] {snippet}")
        return ["\n".join(lines)]

    def _answer_general(self, question: str, chunks: list[Chunk], allowed: list[str]) -> str:
        if not chunks:
            return "Nothing in your docs on that, so I won't guess."
        allowed += [c.text for c in chunks] + [f"[{c.cite}]" for c in chunks]
        det = "From your docs:\n" + "\n".join(f"- [{c.cite}] {' '.join(c.text.split())[:240]}" for c in chunks)
        if not self.llm:
            return det
        ctx = "\n".join(f"[{c.cite}] {c.text}" for c in chunks)
        user = (
            f"OPERATOR ASKED: {question}\n\nEXCERPTS:\n{ctx}\n\n"
            "Answer in 1-3 short sentences using only the excerpts, citing [file p.N]. If they don't answer it, say so. "
            "No numbers that aren't in the excerpts. Do not write COMMIT or SAFEGUARD lines. Do not ask a question."
        )
        try:
            body = self.llm.explain(SYSTEM_PROMPT, user)
        except LLMError:
            return det
        body = re.sub(r"(?m)^\s*(COMMIT|SAFEGUARD):.*$", "", body).strip()
        if not body or audit_numbers(body, allowed + [question]):
            return det
        return body

    _last_rejected = False

    def _llm_explain(self, text: str, st: Status, chunks: list[Chunk], allowed: list[str]) -> str | None:
        self._last_rejected = False
        ctx = "\n".join(f"[{c.cite}] {c.text}" for c in chunks) or "(none)"
        user = (
            f"OPERATOR SAID: {text}\n\nENGINE (authoritative):\n{st.text}\nQuestion to ask: {st.question}\n\n"
            f"EXCERPTS:\n{ctx}\n\n{EXPLAIN_RULES}"
        )
        try:
            body = self.llm.explain(SYSTEM_PROMPT, user)  # type: ignore[union-attr]
        except LLMError:
            return None
        body = re.sub(r"(?m)^\s*(COMMIT|SAFEGUARD):.*$", "", body).strip()
        if not body or body.count("?") > 1 or OUTCOME_WORDS.search(body) or audit_numbers(body, allowed):
            self._last_rejected = True
            return None
        return body
