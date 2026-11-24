"""`ce-meta` command line: build the reference tables the service reads."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ce_ingest.writers import ParquetWriter
from ce_meta.build.geo import (
    CROSSWALK_TABLE,
    GEO_TABLE,
    build_crosswalk,
    check_geography,
)
from ce_meta.build.reference import CODES_TABLE, SURVEY, build_reference
from ce_meta.config import MetaSettings

UNMATCHED_TABLE = "unmatched_codes"


def _build_dictionary(writer: ParquetWriter, dictionary: Path, release: int) -> int:
    if not dictionary.exists():
        print(f"error: no dictionary at {dictionary}", file=sys.stderr)
        return 1
    for name, rows in build_reference(writer, dictionary, release).items():
        print(f"built  ref/{name}: {rows} rows")
    return 0


def _build_crosswalk(writer: ParquetWriter, release: int) -> int:
    codes = writer.read_rows(CODES_TABLE, "ref")
    if codes is None:
        print("error: ref/codes is not built; run the dictionary step", file=sys.stderr)
        return 1
    source = writer.duckdb_from(SURVEY, GEO_TABLE)
    crosswalk = build_crosswalk(codes, source, release)
    writer.replace_table("ref", CROSSWALK_TABLE, crosswalk)
    print(f"built  ref/{CROSSWALK_TABLE}: {crosswalk.num_rows} rows")
    if source is None:
        print(
            f"       no {GEO_TABLE} data is loaded: PSU parents and the check skipped"
        )
        return 0

    listed = writer.read_rows(UNMATCHED_TABLE)
    unmatched = (
        {
            (r["column_name"], r["value"])
            for r in listed.to_pylist()
            if r["table"] == GEO_TABLE
        }
        if listed is not None
        else set()
    )
    check = check_geography(crosswalk, source, release, unmatched)
    print(
        f"check  {check.checked} records compared, {check.excluded} left out "
        "(a blank value, or region C)"
    )
    for value in check.unresolved:
        print(f"       not a code of the release: {value}")
    for conflict in check.conflicts:
        print(f"       CONFLICT {conflict}")
    return 0 if check.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    settings = MetaSettings()
    parser = argparse.ArgumentParser(prog="ce-meta")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build the reference tables under ref/")
    build.add_argument(
        "--step", choices=["dictionary", "crosswalk", "all"], default="all"
    )
    build.add_argument("--data", type=Path, default=settings.data_dir)
    build.add_argument("--dictionary", type=Path, default=settings.dictionary)
    build.add_argument("--release", type=int, default=settings.release)
    args = parser.parse_args(argv)

    writer = ParquetWriter(args.data)
    status = 0
    if args.step in ("dictionary", "all"):
        status = _build_dictionary(writer, args.dictionary, args.release)
    if status == 0 and args.step in ("crosswalk", "all"):
        status = _build_crosswalk(writer, args.release)
    return status


if __name__ == "__main__":
    sys.exit(main())
