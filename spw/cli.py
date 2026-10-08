from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from spw import DEFAULT_DB_PATH, DIAG_DB_PATH, DIST_DIR, KB_PATH, PACKS_DIR
from spw.decode import DecodeError, decode_vin
from spw.export import write_exports
from spw.lookup import donor_pool, format_donor_pool, parse_lookup_query
from spw.parse import apply_curated_sql, parse_pdf
from spw.sop import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    PRICES,
    collect_batches,
    export_sops,
    load_targets,
    pending_targets,
    plan,
    run_sample,
    submit_batch,
)
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

    ingest_parser = subparsers.add_parser("ingest", help="build knowledge.db from a docs directory (needs .[ingest])")
    ingest_parser.add_argument("source")
    ingest_parser.add_argument("--kb", default=str(KB_PATH))

    packs_parser = subparsers.add_parser("packs", help="gate pack integrity + UNVERIFIED count against knowledge.db")
    packs_parser.add_argument("--kb", default=str(KB_PATH))
    packs_parser.add_argument("--packs", default=str(PACKS_DIR))

    chat_parser = subparsers.add_parser("chat", help="terminal diagnostic chat grounded in knowledge.db")
    chat_parser.add_argument("--kb", default=str(KB_PATH))
    chat_parser.add_argument("--packs", default=str(PACKS_DIR))
    chat_parser.add_argument("--diag-db", default=str(DIAG_DB_PATH))
    chat_parser.add_argument("--offline", action="store_true", help="deterministic mode: no LLM phrasing")

    serve_parser = subparsers.add_parser("serve", help="local web chat on localhost")
    serve_parser.add_argument("--kb", default=str(KB_PATH))
    serve_parser.add_argument("--packs", default=str(PACKS_DIR))
    serve_parser.add_argument("--diag-db", default=str(DIAG_DB_PATH))
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--offline", action="store_true")

    sop_parser = subparsers.add_parser("sop")
    sop_subparsers = sop_parser.add_subparsers(dest="sop_command", required=True)
    for name in ("plan", "sample", "submit"):
        sub = sop_subparsers.add_parser(name)
        sub.add_argument("--scope", action="append", choices=["engine-loop", "platform"])
        sub.add_argument("--cluster", action="append", help="limit to these cluster ids")
        sub.add_argument("--limit", type=int)
        sub.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(PRICES))
        sub.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
        sub.add_argument("--db")
    sop_subparsers.choices["sample"].set_defaults(limit=5)
    sop_subparsers.choices["submit"].add_argument("--yes", action="store_true", help="skip the cost confirmation prompt")
    sop_collect = sop_subparsers.add_parser("collect")
    sop_collect.add_argument("--db")
    sop_export = sop_subparsers.add_parser("export")
    sop_export.add_argument("--db")
    sop_export.add_argument("--dist", default=str(DIST_DIR))

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
    if args.command == "ingest":
        from spw.ingest import ingest_dir, stats

        report = ingest_dir(args.source, args.kb)
        print(json.dumps({"report": report.as_dict(), "knowledge_db": stats(args.kb)}, indent=2))
        return 0
    if args.command == "packs":
        from spw.gates.pack import PackError, load_packs
        from spw.gates.report import unverified_report

        try:
            load_packs(args.packs)
        except PackError as exc:
            print(f"pack integrity failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(unverified_report(args.packs, args.kb), indent=2))
        return 0
    if args.command == "chat":
        return _chat(args)
    if args.command == "serve":
        from spw.serve import serve

        serve(args.kb, args.packs, args.diag_db, args.port, offline=args.offline)
        return 0
    if args.command == "sop":
        return _sop(args)
    parser.error("unknown command")
    return 2


def _chat(args: argparse.Namespace) -> int:
    from spw.chat import ChatSession
    from spw.gates.pack import load_packs
    from spw.llm import default_llm

    packs = load_packs(args.packs)
    if not packs:
        print(f"no gate packs in {args.packs}", file=sys.stderr)
        return 1
    session = ChatSession(packs, args.kb, llm=None if args.offline else default_llm(), diag_db=args.diag_db)
    print(f"spw chat ({session.mode} mode). Say the symptom and vehicle. 'quit' to leave.\n")
    while True:
        try:
            line = input("you> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if line.strip().lower() in {"quit", "exit"}:
            return 0
        if line.strip():
            print("\n" + session.turn(line).reply + "\n")


def _anthropic_client():
    try:
        import anthropic
    except ImportError:
        print("spw sop needs the Anthropic SDK: pip install -e .[sop]", file=sys.stderr)
        raise SystemExit(1) from None
    return anthropic.Anthropic()


def _sop(args: argparse.Namespace) -> int:
    db_path = _db_path(args.db)
    if args.sop_command == "collect":
        print(json.dumps(collect_batches(_anthropic_client(), db_path), indent=2))
        return 0
    if args.sop_command == "export":
        print(json.dumps(export_sops(db_path, args.dist), indent=2))
        return 0
    scopes = tuple(args.scope or ("engine-loop", "platform"))
    targets = pending_targets(load_targets(scopes=scopes, clusters=set(args.cluster or [])), db_path)
    if args.limit is not None and args.sop_command == "sample" and targets:
        # Spread the sample across clusters and both scopes so the measured cost is representative.
        step = max(1, len(targets) // args.limit)
        targets = targets[::step][: args.limit]
    elif args.limit is not None:
        targets = targets[: args.limit]
    estimate = plan(targets, args.model)
    if args.sop_command == "plan":
        print(json.dumps(estimate, indent=2))
        return 0
    if not targets:
        print("nothing to generate: every selected SOP is stored or in flight")
        return 0
    if args.sop_command == "sample":
        print(json.dumps(run_sample(_anthropic_client(), targets, db_path, args.model, args.effort), indent=2))
        return 0
    print(json.dumps(estimate, indent=2))
    if not args.yes and input("submit batch? [y/N] ").strip().lower() != "y":
        print("aborted")
        return 1
    print(json.dumps(submit_batch(_anthropic_client(), targets, db_path, args.model, args.effort), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
