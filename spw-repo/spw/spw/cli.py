"""CLI entrypoint for Service Plan Writer."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from spw import __version__
from spw.parse import parse, apply_curated
from spw.validate import validate
from spw.lookup import donor_pool, format_pool
from spw.decode import decode, format_decode
from spw.verdicts import add_verdict, report
from spw.export import to_json, to_csv

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "clone_clusters.db"
ASSETS = ROOT / "assets"
DIST = ROOT / "dist"


def cmd_parse(args: argparse.Namespace) -> None:
    pdf = Path(args.pdf) if args.pdf else ASSETS / "EDC-1057.pdf"
    db = Path(args.db) if args.db else DEFAULT_DB
    print(f"Parsing {pdf} → {db}")
    stats = parse(pdf, db)
    print(
        f"  raw: {stats['raw']} | unique: {stats['unique']} | "
        f"clusters: {stats['clusters']} | skipped: {stats['skipped']} "
        f"({stats['skipped_ratio']:.1%})"
    )
    if stats["skipped_ratio"] > 0.01:
        print("  ⚠ GATE 1 FAIL: skipped ratio > 1%", file=sys.stderr)
        sys.exit(1)
    if stats["unique"] < 1800:
        print(f"  ⚠ GATE 1 FAIL: unique vehicles {stats['unique']} < 1800", file=sys.stderr)
        sys.exit(1)
    # apply curated
    sql = ASSETS / "curated.sql"
    n = apply_curated(db, sql)
    print(f"  curated: {n} memberships applied from {sql.name}")
    print("  ✓ Gate 1 passed.")


def cmd_validate(args: argparse.Namespace) -> None:
    db = Path(args.db) if args.db else DEFAULT_DB
    print(f"Validating {db}")
    result = validate(db)
    print(f"  {result['flagged']}/{result['total']} structural clusters flagged → verdicts logged")
    print("  ✓ Gate 4 passed.")


def cmd_lookup(args: argparse.Namespace) -> None:
    db = Path(args.db) if args.db else DEFAULT_DB
    # parse "2008 BMW 528i" style input
    parts = args.vehicle.strip().split()
    if len(parts) < 3:
        print("Usage: spw lookup \"YEAR MAKE MODEL\"", file=sys.stderr)
        sys.exit(1)
    year = int(parts[0])
    make = parts[1]
    model = " ".join(parts[2:])
    donors = donor_pool(make, model, year, db)
    print(f"DONOR POOL — {year} {make} {model}\n")
    print(format_pool(donors))


def cmd_decode(args: argparse.Namespace) -> None:
    try:
        rec = decode(args.vin)
        print(format_decode(rec))
    except Exception as e:
        print(f"Decode failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_verdict(args: argparse.Namespace) -> None:
    db = Path(args.db) if args.db else DEFAULT_DB
    if args.action == "add":
        rid = add_verdict(db, args.part, args.method, args.cluster, args.result, args.notes or "")
        print(f"Verdict #{rid} logged.")
    elif args.action == "report":
        print(report(db))


def cmd_build(args: argparse.Namespace) -> None:
    db = Path(args.db) if args.db else DEFAULT_DB
    template = ROOT / "web" / "template.html"
    out = DIST / "spw.html"
    DIST.mkdir(exist_ok=True)

    data = to_json(db)
    html = template.read_text(encoding="utf-8")
    html = html.replace("__DATA__", data)
    out.write_text(html, encoding="utf-8")
    size_kb = out.stat().st_size / 1024
    print(f"Built {out} ({size_kb:.0f} KB)")
    if size_kb > 400:
        print(f"  ⚠ GATE 6 WARNING: file size {size_kb:.0f} KB > 400 KB", file=sys.stderr)

    # also export CSV
    csv_path = DIST / "vehicles_flat.csv"
    n = to_csv(db, csv_path)
    print(f"Exported {csv_path} ({n} rows)")
    print("  ✓ Build complete.")


def main() -> None:
    p = argparse.ArgumentParser(prog="spw", description="Service Plan Writer")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--db", help="Path to clone_clusters.db", default=None)
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("parse", help="Parse EDC-1057 PDF into SQLite")
    sp.add_argument("pdf", nargs="?", help="Path to EDC-1057.pdf")

    sub.add_parser("validate", help="Run Gate 4 consistency checks")

    sl = sub.add_parser("lookup", help="Donor pool lookup")
    sl.add_argument("vehicle", help="'YEAR MAKE MODEL' e.g. '2008 BMW 528i'")

    sd = sub.add_parser("decode", help="Decode a VIN via NHTSA vPIC")
    sd.add_argument("vin", help="17-character VIN")

    sv = sub.add_parser("verdict", help="Verdict log management")
    sv_sub = sv.add_subparsers(dest="action")
    sva = sv_sub.add_parser("add", help="Add a verdict")
    sva.add_argument("--part", required=True)
    sva.add_argument("--method", required=True)
    sva.add_argument("--cluster", required=True)
    sva.add_argument("--result", required=True, choices=["pass", "fail"])
    sva.add_argument("--notes", default="")
    sv_sub.add_parser("report", help="Print verdict report")

    sub.add_parser("build", help="Export JSON + build dist/spw.html")

    args = p.parse_args()
    if not args.command:
        p.print_help()
        sys.exit(1)

    cmds = {
        "parse": cmd_parse, "validate": cmd_validate,
        "lookup": cmd_lookup, "decode": cmd_decode,
        "verdict": cmd_verdict, "build": cmd_build,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
