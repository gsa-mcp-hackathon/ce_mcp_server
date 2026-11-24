"""`ce-capture` command line: the schema files, the gold set and the Delta
record tables."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ce_capture.records import SCHEMA_DIR, SCHEMAS, schema_text, write_schemas


def _cmd_schemas(args: argparse.Namespace) -> int:
    if args.check:
        stale = [
            name
            for name, model in SCHEMAS.items()
            if not (SCHEMA_DIR / name).exists()
            or (SCHEMA_DIR / name).read_text() != schema_text(model)
        ]
        for name in stale:
            print(f"out of date: {SCHEMA_DIR / name}")
        return 1 if stale else 0
    for path in write_schemas():
        print(f"wrote {path}")
    return 0


def _context():
    from ce_capture.context import CaptureContext
    from ce_mcp_server.service import build_service

    return CaptureContext.from_service(build_service())


def _cmd_gold(args: argparse.Namespace) -> int:
    from ce_capture.gold import check_gold, load_gold

    results = check_gold(_context(), load_gold(args.gold))
    failed = 0
    for r in results:
        mark = "ok  " if not r.problems else "FAIL"
        print(f"{mark} {r.case}")
        for problem in r.problems:
            print(f"       {problem}")
        failed += bool(r.problems)
    print(f"{len(results)} cases, {failed} failed")
    return 1 if failed else 0


def _cmd_score(args: argparse.Namespace) -> int:
    from ce_capture.config import CaptureSettings
    from ce_capture.databricks import DatabricksEndpoint
    from ce_capture.gold import load_gold, score

    settings = CaptureSettings()
    endpoint = args.endpoint or settings.endpoint
    if not endpoint:
        print(
            "error: no endpoint: set CE_CAPTURE_ENDPOINT or pass --endpoint",
            file=sys.stderr,
        )
        return 1
    try:  # the outcome level comes from the evaluation package, when installed
        from ce_eval.signature import outcome_score
    except ImportError:
        outcome_score = None
    report = score(
        _context(),
        DatabricksEndpoint(endpoint, settings),
        load_gold(args.gold),
        args.repeat,
        outcome=outcome_score,
    )
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    print(json.dumps(report["summary"], indent=2))
    return 0


def _cmd_store_ddl(args: argparse.Namespace) -> int:
    from ce_capture.config import CaptureSettings
    from ce_capture.delta_store import DeltaRecordStore
    from ce_capture.warehouse import StoreError

    settings = CaptureSettings()
    names = (
        args.catalog or settings.store_catalog or "",
        args.schema or settings.store_schema or "",
        settings.store_prefix if args.prefix is None else args.prefix,
    )
    try:
        if args.apply:
            store = DeltaRecordStore.from_settings(
                settings.model_copy(
                    update=dict(
                        zip(
                            ("store_catalog", "store_schema", "store_prefix"),
                            names,
                            strict=True,
                        )
                    )
                )
            )
        else:  # no warehouse is contacted: the statements are only printed
            store = DeltaRecordStore(None, *names)  # type: ignore[arg-type]
        statements = store.ddl()
        if args.grant_to:
            statements += store.grants(args.grant_to)
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not args.apply:
        print(";\n\n".join(statements) + ";")
        return 0
    failed = 0
    for statement in statements:
        first = statement.splitlines()[0].rstrip(" (")
        try:
            store.warehouse.execute(statement)
            print(f"ok    {first}")
        except StoreError as exc:
            failed += 1
            print(f"FAIL  {first}\n      {exc}")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ce-capture")
    sub = parser.add_subparsers(dest="command", required=True)
    schemas = sub.add_parser("schemas", help="write the record JSON Schemas")
    schemas.add_argument("--check", action="store_true", help="only check them")
    gold = sub.add_parser("gold", help="check the gold set with no model")
    gold.add_argument("--gold", type=Path, default=Path("eval/cases/core.yaml"))
    sc = sub.add_parser("score", help="score the server-side model on the gold set")
    sc.add_argument("--gold", type=Path, default=Path("eval/cases/core.yaml"))
    sc.add_argument("--endpoint")
    sc.add_argument("--repeat", type=int, default=3)
    sc.add_argument("--out", type=Path)
    ddl = sub.add_parser(
        "store-ddl", help="print the SQL that creates the Delta record tables"
    )
    ddl.add_argument("--catalog", help="default: CE_CAPTURE_STORE_CATALOG")
    ddl.add_argument("--schema", help="default: CE_CAPTURE_STORE_SCHEMA")
    ddl.add_argument("--prefix", help="default: CE_CAPTURE_STORE_PREFIX")
    ddl.add_argument(
        "--grant-to",
        help="also print the grants for this principal, such as the "
        "application ID of the app's service principal",
    )
    ddl.add_argument(
        "--apply",
        action="store_true",
        help="run the statements on the SQL warehouse instead of printing them",
    )
    args = parser.parse_args(argv)
    if args.command == "store-ddl":
        return _cmd_store_ddl(args)
    if args.command == "schemas":
        return _cmd_schemas(args)
    if args.command == "gold":
        return _cmd_gold(args)
    return _cmd_score(args)


if __name__ == "__main__":
    sys.exit(main())
