# spw

Service Plan Writer (SPW) is a local-first service plan generator for DIY mechanics.
It resolves a vehicle, looks up deterministic donor pools from SQLite, and produces a
printable single-file HTML runbook with optional AI SOP generation.

## Quickstart

```bash
make all
spw lookup "2008 BMW 528i"
open dist/spw.html
```

## Commands

```bash
spw parse assets/EDC-1057.pdf  # optional; falls back to bundled seed dataset if missing
spw validate
spw lookup "2010 Chevrolet Tahoe"
spw decode WBANU53598CT10444
spw verdict add --part alternator --method rockauto --cluster N52-LOOP --result pass
spw verdict report
spw build
```

## SOP library (Anthropic Batch API)

Pre-generates one SOP per cluster × job from `vehicles_flat.csv` (engine-loop clusters ×
engine jobs, platform clusters × chassis jobs) and stores results in the SQLite db.
Requires `pip install -e .[sop]` and `ANTHROPIC_API_KEY`.

```bash
spw sop plan                  # request count + estimated batch cost, no API calls
spw sop sample --limit 5      # 5 synchronous calls at full price; reports real cost per SOP
spw sop submit                # one batch (50% price) for everything not yet stored
spw sop collect               # fetch finished batches; only end_turn results are marked ok
spw sop export                # dist/sops/<cluster>/<job>.md + dist/sops/index.json
```

Re-running `submit` only sends SOPs that are missing, truncated, or errored.

## Diagnostic chat (local RAG + deterministic gate engine)

Conversational troubleshooting grounded in your own manuals and SOPs. The gate engine decides every
branch; retrieval supplies cited context; the LLM only phrases and (second to regex) extracts. Readings
are echoed back and nothing counts until you confirm. RISK FLAG steps need an explicit `ack`.

```bash
pip install -e .[ingest]        # build-time only: pypdf, pandas, openpyxl
spw ingest docs/                # PDF / MD / TXT -> chunks + FTS5; CSV / XLSX -> cited spec rows (assets/knowledge.db)
spw packs                       # pack integrity + how many numeric gates are still UNVERIFIED
spw chat                        # terminal REPL   (add --offline for deterministic mode, no LLM)
spw serve                       # http://127.0.0.1:8765, localhost only
```

- Spec tables need `quantity` plus `value` or `min`/`max`; optional `make, model, years, ecu_family, unit, measurement_state, page`.
  A gate's `spec_ref` is `<ecu_family>.<quantity>_<state>` slugged (e.g. `marelli.ecu_supply_koeo`).
- Resolution order: specs table for this vehicle -> cited literal in the pack -> `UNVERIFIED`. Disagreeing sources are shown side by side and the operator picks.
- LLM is used only with `ANTHROPIC_API_KEY` set (`SPW_CHAT_MODEL` to override the model). No key or no network: gate prompts and retrieved snippets are shown verbatim.
- Every turn appends `COMMIT:` / `SAFEGUARD:` rows to `assets/diag.db` (`diag_events`); contexts include `CHAT-Q`, `CHAT-CONFIRM`, `PENDING-<gate>`.
- Packs live in `packs/*.json`. Pack prose may not contain digits; numbers come from specs only.
