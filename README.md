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
