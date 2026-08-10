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
spw compile                            # re-apply the compiled overlay (no re-parse)
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
| `spw/compiled.py` | Compiled overlay loader — modern vehicles, platforms, engine families |
| `spw/validate.py` | Structural cluster consistency checks (Gate 4) |
| `spw/lookup.py` | Donor pool query with model-matching rules |
| `spw/decode.py` | NHTSA vPIC VIN decoder |
| `spw/verdicts.py` | Calibration feedback log |
| `spw/export.py` | DB → JSON (for HTML embed) / CSV |
| `assets/curated.sql` | Hand-verified engine family clusters (immutable across re-parse) |
| `assets/compiled_clusters.csv` | Platform + engine-family cluster definitions |
| `assets/compiled_vehicles.csv` | 2013-2025 vehicles with their platform/engine tags |
| `assets/compiled_legacy.csv` | Rules attaching engine families to EDC-era vehicles |
| `web/template.html` | Frontend with `__DATA__` placeholder |
| `dist/spw.html` | Built artifact — single self-contained file, works offline |

## Data Confidence Tiers

Tiers run strongest to weakest. The donor table tags every row with its tier.

- **`edc`** — EDC-1057 structural interchange (verified by crash reconstruction authority)
- **`edc-flagged`** — EDC structural, failed consistency check (logged in verdicts)
- **`curated`** — Hand-verified engine family clusters (J37-LOOP, J35-ADJ, N52-LOOP)
- **`compiled`** — Platform and engine-family data compiled from published engine-application
  and platform-sharing tables. **Not authority-verified.** Every engine link carries a trim
  qualifier (`5.3 L83 / 6.2 L86 — 4.3 V6 excluded`); read it before ordering, and confirm
  against the donor vehicle's own VIN.

## Coverage

| | Vehicles | Model years | With an engine-family link |
|---|---|---|---|
| EDC-1057 only | 1,885 | 1974-2013 | 30 |
| With compiled overlay | 2,198 | 1974-2025 | 604 |

The EDC source carries no engine data and stops at 2013, so on its own the donor pool is
empty for anything built in the last decade. The compiled overlay adds 89 engine families,
57 platform clusters and 313 modern vehicles, and back-fills engine families onto EDC-era
vehicles through `compiled_legacy.csv`.

## Adding Engine Families

**Hand-verified (`curated`):** edit `assets/curated.sql`, add INSERT statements, re-run
`make all`. The parser never touches curated rows.

**Compiled (`compiled`):** edit the CSVs in `assets/`.

1. Define the cluster in `compiled_clusters.csv` — `cluster_id,type,label`, where type is
   `structural` (platform) or `engine_family`.
2. Tag modern vehicles in `compiled_vehicles.csv`. The `platform` and `engines` columns take
   `CLUSTER:qualifier` entries, `;`-separated — e.g.
   `PENTASTAR-36:3.6 V6;HEMI-57:5.7 V8 only`.
3. Attach the family to EDC-era vehicles in `compiled_legacy.csv` — one rule per line,
   `cluster_id,make,model_like,year_from,year_to,note`, where `model_like` is a SQL LIKE
   pattern matched against the EDC model string.
4. Run `spw compile`. It reports any rule that matched nothing and fails on any reference to
   an undefined cluster. Re-running is idempotent — compiled rows are replaced wholesale and
   the EDC and curated tiers are left untouched.

## ⚠ Spec Verification

AI-generated torque specs and fluid capacities are **UNVERIFIED**. Always cross-reference against the factory service manual before final tightening.
