"""`ce-sidecars` command line: generate table sidecars and check the set."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ce_ingest.writers import ParquetWriter
from ce_meta.sidecars import MASTER_FILE, load_set, validate
from ce_sidecars.build import (
    EXPN_PREFIX,
    dictionary_file,
    dump_sidecar,
    generate_table_sidecar,
    load_yaml,
)
from ce_sidecars.dictionary import read_dictionary
from ce_sidecars.generate import Review, review_markdown
from ce_sidecars.master import sync_master

DEFAULT_DICTIONARY = Path("../sample_data/ce-pumd-interview-diary-dictionary-2.xlsx")
DEFAULT_EXPN_VERSION = "0.1.0"


def _generate_one(
    args: argparse.Namespace, table: str, version: str, row_key: list[str] | None
) -> tuple[Path, int, Review]:
    """Build one table sidecar and its review list, and write both."""
    sidecars: Path = args.sidecars
    table_id = f"ce-interview-{table}"
    master = load_yaml(sidecars / MASTER_FILE)
    if master is None:
        raise ValueError(f"no master sidecar in {sidecars}")
    overrides = load_yaml(sidecars / "overrides" / f"{table_id}.yaml")
    dictionary = read_dictionary(args.dictionary, dictionary_file(table), args.release)
    doc, review = generate_table_sidecar(
        ParquetWriter(args.data),
        args.survey,
        table,
        dictionary,
        master,
        overrides,
        version,
        row_key,
    )
    path = sidecars / f"{table_id}.odcs.yaml"
    path.write_text(dump_sidecar(doc, table, args.release))
    total = len(doc["schema"][0]["properties"])
    (sidecars / f"{table_id}.review.md").write_text(
        review_markdown(table, version, review, total)
    )
    return path, total, review


def _cmd_generate(args: argparse.Namespace) -> int:
    row_key = args.row_key.split(",") if args.row_key else None
    try:
        path, total, review = _generate_one(args, args.table, args.version, row_key)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {path}: {total} columns")
    for reason, cols in sorted(review.items.items(), key=lambda kv: -len(kv[1])):
        print(f"  review: {len(cols):4}  {reason}")
    return 0


def _expn_versions(args: argparse.Namespace) -> dict[str, str]:
    """Stored detailed expenditure table -> the version its sidecar has, or the
    version to give a new one."""
    stored = sorted(
        table
        for survey, table in ParquetWriter(args.data).tables(args.survey)
        if table.startswith(EXPN_PREFIX)
    )
    versions = {}
    for table in stored:
        existing = load_yaml(args.sidecars / f"ce-interview-{table}.odcs.yaml")
        versions[table] = str(existing["version"]) if existing else args.version
    return versions


def _cmd_sync_master(args: argparse.Namespace) -> int:
    """List exactly the stored detailed expenditure tables in the master."""
    path = args.sidecars / MASTER_FILE
    versions = _expn_versions(args)
    try:
        text = sync_master(path.read_text(), versions)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    path.write_text(text)
    print(f"{path}: lists {len(versions)} detailed expenditure tables")
    return 0


def _cmd_generate_expn(args: argparse.Namespace) -> int:
    """Sync the master, then generate every detailed expenditure sidecar."""
    if _cmd_sync_master(args):
        return 1
    failed = 0
    for table, version in _expn_versions(args).items():
        try:
            _path, total, review = _generate_one(args, table, version, None)
        except ValueError as exc:
            print(f"error: {table}: {exc}", file=sys.stderr)
            failed += 1
            continue
        open_items = sum(len(cols) for cols in review.items.values())
        print(f"{table:<10} {total:4} columns, {open_items:4} review items")
    return 1 if failed else 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Run the set checks. Any failure exits 1, so CI fails on it."""
    report = validate(load_set(args.sidecars), args.odcs_schema)
    for line in report.lines():
        print(line)
    return 0 if report.ok else 1


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--survey", default="intrvw", choices=["intrvw", "diary"])
    parser.add_argument("--release", type=int, default=2025)
    parser.add_argument("--data", type=Path, default=Path("data/parquet"))
    parser.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY)
    parser.add_argument("--sidecars", type=Path, default=Path("sidecars"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ce-sidecars")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="generate a table sidecar from scratch")
    gen.add_argument("--table", required=True, help="stored table, e.g. fmli")
    gen.add_argument("--version", required=True, help="sidecar version, e.g. 0.4.0")
    gen.add_argument(
        "--row-key", help="comma-separated row key; default: the overrides file"
    )
    _common(gen)

    expn = sub.add_parser(
        "generate-expn",
        help="sync the master, then generate every detailed expenditure sidecar",
    )
    expn.add_argument(
        "--version", default=DEFAULT_EXPN_VERSION, help="version for a new sidecar"
    )
    _common(expn)

    sync = sub.add_parser(
        "sync-master",
        help="list the stored detailed expenditure tables in the master sidecar",
    )
    sync.add_argument(
        "--version", default=DEFAULT_EXPN_VERSION, help="version for a new sidecar"
    )
    _common(sync)

    val = sub.add_parser("validate", help="check the sidecar set as a whole")
    val.add_argument("--sidecars", type=Path, default=Path("sidecars"))
    val.add_argument(
        "--odcs-schema", type=Path, help="the ODCS v3.2.0 JSON Schema, if at hand"
    )

    args = parser.parse_args(argv)
    commands = {
        "generate": _cmd_generate,
        "generate-expn": _cmd_generate_expn,
        "sync-master": _cmd_sync_master,
        "validate": _cmd_validate,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
