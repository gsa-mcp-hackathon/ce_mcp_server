"""`ce-measures`: the outcome schema, and processing a stored request locally.

ce-measures schema [--check]        write or check the outcome record schema
ce-measures evaluate REQUEST_ID     judge a captured request; print the reply
ce-measures run REQUEST_ID          judge and compute it; print the reply
"""

from __future__ import annotations

import argparse
import sys

from ce_measures.outcome import SCHEMA_FILE, schema_text, write_schema


def _processor():
    from ce_capture.context import CaptureContext
    from ce_capture.storage import open_store
    from ce_mcp_server.service import build_service
    from ce_measures.process import RequestProcessor
    from ce_meta.config import MetaSettings

    meta = MetaSettings()
    service = build_service(meta)
    ctx = CaptureContext.from_service(service)
    return RequestProcessor(ctx, open_store(), meta.data_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ce-measures")
    sub = parser.add_subparsers(dest="command", required=True)
    schema = sub.add_parser("schema", help="write or check the outcome schema")
    schema.add_argument("--check", action="store_true")
    for name in ("evaluate", "run"):
        p = sub.add_parser(name, help=f"{name} a captured request")
        p.add_argument("request_id")
        p.add_argument("--resolved-id")
    args = parser.parse_args(argv)

    if args.command == "schema":
        if args.check:
            ok = SCHEMA_FILE.exists() and SCHEMA_FILE.read_text() == schema_text()
            print(
                "up to date" if ok else f"stale: run ce-measures schema ({SCHEMA_FILE})"
            )
            return 0 if ok else 1
        print(write_schema())
        return 0

    from ce_measures.errors import CeMeasuresError

    processor = _processor()
    fn = processor.evaluate if args.command == "evaluate" else processor.run
    try:
        record, text = fn(args.request_id, args.resolved_id)
    except CeMeasuresError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    print(text)
    print(f"outcome {record.outcome_id}: {record.verdict}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
