# swp

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
spw parse assets/EDC-1057.pdf
spw validate
spw lookup "2010 Chevrolet Tahoe"
spw decode WBANU53598CT10444
spw verdict add --part alternator --method rockauto --cluster N52-LOOP --result pass
spw verdict report
spw build
```
