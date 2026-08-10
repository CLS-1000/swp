from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from spw import DEFAULT_DB_PATH, DIST_DIR
from spw.decode import DecodeError, decode_vin
from spw.export import write_exports
from spw.lookup import donor_pool, format_donor_pool, parse_lookup_query
from spw.parse import apply_curated_sql, parse_pdf
from spw.validate import ValidationError, validate_database
from spw.verdicts import add_verdict, report_verdicts
from web.build import build_html_bundle


def _db_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_DB_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_parser = subparsers.add_parser("parse")
    parse_parser.add_argument("source")
    parse_parser.add_argument("--db")

    curated_parser = subparsers.add_parser("curated")
    curated_parser.add_argument("--db")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--db")

    lookup_parser = subparsers.add_parser("lookup")
    lookup_parser.add_argument("query")
    lookup_parser.add_argument("--db")

    decode_parser = subparsers.add_parser("decode")
    decode_parser.add_argument("vin")

    verdict_parser = subparsers.add_parser("verdict")
    verdict_subparsers = verdict_parser.add_subparsers(dest="verdict_command", required=True)
    verdict_add = verdict_subparsers.add_parser("add")
    verdict_add.add_argument("--part", required=True)
    verdict_add.add_argument("--method", required=True)
    verdict_add.add_argument("--cluster", required=True)
    verdict_add.add_argument("--result", required=True)
    verdict_add.add_argument("--notes", default="")
    verdict_add.add_argument("--db")
    verdict_report = verdict_subparsers.add_parser("report")
    verdict_report.add_argument("--db")

    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--db")
    build_parser.add_argument("--dist", default=str(DIST_DIR))

    args = parser.parse_args(argv)

    if args.command == "parse":
        report = parse_pdf(args.source, _db_path(args.db))
        if not report["source_available"]:
            print(f"warning: {args.source} is not present; using bundled local seed dataset", file=sys.stderr)
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "curated":
        apply_curated_sql(_db_path(args.db))
        print("curated rows applied")
        return 0
    if args.command == "validate":
        try:
            report = validate_database(_db_path(args.db))
        except ValidationError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "lookup":
        make, model, year = parse_lookup_query(args.query)
        print(format_donor_pool(donor_pool(make, model, year, _db_path(args.db))), end="")
        return 0
    if args.command == "decode":
        try:
            print(json.dumps(decode_vin(args.vin), indent=2))
        except DecodeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    if args.command == "verdict" and args.verdict_command == "add":
        add_verdict(args.part, args.method, args.cluster, args.result, args.notes, _db_path(args.db))
        print("verdict added")
        return 0
    if args.command == "verdict" and args.verdict_command == "report":
        print(json.dumps(report_verdicts(_db_path(args.db)), indent=2))
        return 0
    if args.command == "build":
        outputs = write_exports(_db_path(args.db), args.dist)
        html_path = build_html_bundle(_db_path(args.db), Path(args.dist) / "spw.html")
        print(json.dumps({"json": str(outputs["json"]), "csv": str(outputs["csv"]), "html": str(html_path)}, indent=2))
        return 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
