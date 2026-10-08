/* Offline mirror of spw/gates/engine.py. Parity is enforced by tests/test_parity.py. */
(function (root) {
  "use strict";
  var TERM = /^TERMINAL-([A-Z0-9_]+)$/;
  var COND = {KEY_OFF: "key off", KOEO: "key on, engine off", CRANKING: "cranking", RUNNING: "running", UNPLUGGED: "unplugged"};
  function bound(g, s) { if (s.value != null) return s.value; return (g.op === ">=" || g.op === ">") ? s.vmin : s.vmax; }
  function fits(g, s) {
    if (g.type === "window") return s.vmin != null && s.vmax != null;
    if (g.type === "threshold") return bound(g, s) != null;
    return true;
  }
  function evaluate(g, s, v) {
    if (g.type === "boolean") return v ? "YES" : "NO";
    if (!s || !fits(g, s)) throw new Error("no usable spec");
    if (g.type === "window") return v < s.vmin ? "LOW" : v > s.vmax ? "HIGH" : "PASS";
    var L = bound(g, s), ok = {">=": v >= L, ">": v > L, "<=": v <= L, "<": v < L}[g.op];
    return ok ? "PASS" : "FAIL";
  }
  function checkReading(g, r) {
    if (g.type === "boolean") return typeof r.value === "boolean" ? null : "yes/no";
    if (typeof r.value === "boolean") return "numeric";
    if (r.unit && r.unit.toLowerCase() !== g.unit.toLowerCase()) return "unit";
    if (r.state && r.state !== g.condition) return "state";
    return null;
  }
  function Engine(pack, specs) {
    this.pack = pack; this.specs = specs || {};
    this.gates = {}; pack.gates.forEach(function (g) { this.gates[g.id] = g; }, this);
  }
  Engine.prototype.newSession = function () {
    return {intake: false, current: this.pack.start, readings: {}, outcomes: {}, pending: {}, acked: {}, dtcs: false, opSpecs: {}};
  };
  Engine.prototype.spec = function (s, g) {
    if (g.type === "boolean") return {ok: true, spec: null};
    var sp = this.specs[g.id] || s.opSpecs[g.id] || null;
    if (!sp && s.opSpecs[g.id]) sp = s.opSpecs[g.id];
    return {ok: !!sp && fits(g, sp), spec: sp};
  };
  Engine.prototype.blocked = function (s, g) {
    if (g.cuts_power && !s.dtcs) return "NEED_DTCS";
    if (g.risk && !s.acked[g.id]) return "NEED_ACK";
    return "";
  };
  Engine.prototype.advance = function (s) {
    var ev = [];
    while (s.intake && this.gates[s.current]) {
      var g = this.gates[s.current];
      if (this.blocked(s, g)) break;
      if (!s.readings[g.id] && s.pending[g.id]) { s.readings[g.id] = s.pending[g.id]; delete s.pending[g.id]; }
      if (!s.readings[g.id]) break;
      var sp = this.spec(s, g);
      if (!sp.ok) break;
      var out = evaluate(g, sp.spec, s.readings[g.id].value);
      s.outcomes[g.id] = out;
      if (g.captures_dtcs && out === "YES") s.dtcs = true;
      ev.push("DEV-BRANCH-GATE-" + g.id + "-" + out);
      s.current = g.next[out];
      var m = TERM.exec(s.current);
      if (m) { ev.push("DEV-BRANCH-TERMINAL-" + m[1]); break; }
    }
    return ev;
  };
  Engine.prototype.status = function (s) {
    if (!s.intake) return "INTAKE";
    if (TERM.test(s.current)) return "TERMINAL";
    var g = this.gates[s.current], b = this.blocked(s, g);
    if (b) return b;
    if (s.readings[g.id] && !this.spec(s, g).ok) return "NEED_SPEC";
    return "NEED_READING";
  };
  Engine.prototype.completeIntake = function (s) { s.intake = true; return this.advance(s); };
  Engine.prototype.record = function (s, r) {
    var k = this.status(s);
    if (["NEED_READING", "NEED_SPEC", "NEED_CHOICE"].indexOf(k) < 0) return {events: [], error: true};
    var g = this.gates[s.current];
    if (checkReading(g, r)) return {events: [], error: true};
    s.readings[g.id] = r;
    return {events: this.advance(s), error: false};
  };
  Engine.prototype.hold = function (s, id, r) {
    var g = this.gates[id];
    if (!g || s.readings[id] || s.outcomes[id] || checkReading(g, r)) return {events: [], error: true};
    s.pending[id] = r;
    return {events: ["DEV-BRANCH-PENDING-" + id], error: false};
  };
  Engine.prototype.ack = function (s) {
    var g = this.gates[s.current];
    if (!s.intake || !g || !g.risk || s.acked[g.id]) return [];
    s.acked[g.id] = true;
    return ["DEV-BRANCH-RISK-ACK-" + g.id].concat(this.advance(s));
  };
  Engine.prototype.markDtcs = function (s) { s.dtcs = true; return this.advance(s); };
  Engine.prototype.setSpec = function (s, sp) { if (this.gates[s.current]) s.opSpecs[s.current] = sp; return this.advance(s); };
  Engine.prototype.view = function (s) {
    var k = this.status(s), g = this.gates[s.current], m = TERM.exec(s.current);
    if (k === "TERMINAL") return {kind: k, text: (this.pack.terminals || {})[m[1]] || m[1]};
    if (k === "INTAKE") return {kind: k, text: "Pick the flow and start."};
    if (k === "NEED_DTCS") return {kind: k, text: "This step cuts power. Record every DTC first, then confirm."};
    if (k === "NEED_ACK") return {kind: k, text: "RISK FLAG: " + g.risk, ack: true};
    if (k === "NEED_SPEC") return {kind: k, text: "No cited spec — confirm from your manual. UNVERIFIED. Enter the spec you trust.", gate: g};
    var sp = this.spec(s, g).spec, line = "";
    if (g.type !== "boolean") line = "\nSpec: " + (sp ? "" : "UNVERIFIED — no cited spec, confirm from your manual.");
    return {kind: k, gate: g, text: g.instruction + line};
  };
  function simulate(pack, specs, script) {
    var e = new Engine(pack, specs), s = e.newSession(), out = [];
    script.forEach(function (st) {
      var events = [], error = false, res;
      if ("intake" in st) events = e.completeIntake(s);
      else if ("read" in st) { res = e.record(s, {value: st.read, unit: st.unit || null, state: st.state || null}); events = res.events; error = res.error; }
      else if ("hold" in st) { res = e.hold(s, st.hold[0], {value: st.hold[1], unit: st.unit || null, state: st.state || null}); events = res.events; error = res.error; }
      else if ("ack" in st) events = e.ack(s);
      else if ("dtcs_captured" in st) events = e.markDtcs(s);
      else if ("spec" in st) events = e.setSpec(s, st.spec);
      out.push({events: events, error: error, kind: e.status(s), current: s.current});
    });
    return out;
  }
  root.SPWGates = {Engine: Engine, evaluate: evaluate, simulate: simulate, COND: COND};
  if (typeof module !== "undefined") module.exports = root.SPWGates;
})(typeof window !== "undefined" ? window : globalThis);
