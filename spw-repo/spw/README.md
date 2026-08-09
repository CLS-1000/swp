# Service Plan Writer (SPW)

DIY mechanic service plan generator with offline vehicle interchange database.

**Pipeline:** VIN → NHTSA decode → donor pool (offline SQLite) → AI-generated SOP → printable field runbook.

## Quick Start

```bash
pip install httpx pytest
make all          # parse → validate → test → build
```

## CLI

```bash
spw parse                              # rebuild DB from EDC-1057 PDF
spw validate                           # Gate 4 consistency checks
spw lookup "2008 BMW 528i"             # donor pool to stdout
spw decode WBANU53598CT10444           # VIN decode via NHTSA
spw verdict add --part alternator --method rockauto --cluster N52-LOOP --result pass
spw verdict report                     # pass/fail rates
spw build                              # export → dist/spw.html
```

## Architecture

| Component | Function |
|---|---|
| `spw/parse.py` | EDC-1057 PDF → SQLite (Gate 1) |
| `spw/validate.py` | Structural cluster consistency checks (Gate 4) |
| `spw/lookup.py` | Donor pool query with model-matching rules |
| `spw/decode.py` | NHTSA vPIC VIN decoder |
| `spw/verdicts.py` | Calibration feedback log |
| `spw/export.py` | DB → JSON (for HTML embed) / CSV |
| `assets/curated.sql` | Hand-verified engine family clusters (immutable across re-parse) |
| `web/template.html` | Frontend with `__DATA__` placeholder |
| `dist/spw.html` | Built artifact — single self-contained file, works offline |

## Data Confidence Tiers

- **`edc`** — EDC-1057 structural interchange (verified by crash reconstruction authority)
- **`edc-flagged`** — EDC structural, failed consistency check (logged in verdicts)
- **`curated`** — Hand-verified engine family clusters (J37-LOOP, J35-ADJ, N52-LOOP)

## Adding Engine Families

Edit `assets/curated.sql`, add INSERT statements, re-run `make all`. Parser never touches curated rows.

## ⚠ Spec Verification

AI-generated torque specs and fluid capacities are **UNVERIFIED**. Always cross-reference against the factory service manual before final tightening.
