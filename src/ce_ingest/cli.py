"""`ce-ingest` command line: `files`, `load` and `profile`."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from ce_ingest.config import IngestSettings
from ce_ingest.filename import discover_files, discover_rejects
from ce_ingest.pipeline import load_release
from ce_ingest.profile import finish_profile, profile_table
from ce_ingest.registry import loaded_release
from ce_ingest.writers import get_writer


def _cmd_files(settings: IngestSettings, survey: str | None) -> int:
    files = discover_files(settings.landing_dir, survey)  # type: ignore[arg-type]
    for src in files:
        print(
            f"{src.survey:6}  {src.table:4}  {src.survey_year}  "
            f"{src.quarter_code or '-':2}  {src.path}"
        )
    for path, reason in discover_rejects(settings.landing_dir, survey):  # type: ignore[arg-type]
        print(f"skipped  {path}: {reason}")
    print(f"{len(files)} file(s)")
    return 0


def _cmd_load(
    settings: IngestSettings, survey: str | None, year: int | None, profile: bool
) -> int:
    results = load_release(settings, survey, year, profile)  # type: ignore[arg-type]
    for r in results:
        line = f"{r.status:20}  {r.row_count:>8}  {r.source.label}"
        if r.warnings:
            line += "  (" + "; ".join(r.warnings) + ")"
        if r.error:
            line += f"  ERROR: {r.error}"
        print(line)
    failed = sum(r.status == "failed" for r in results)
    print(f"{len(results)} file(s), {failed} failed")
    return 1 if failed else 0


def _cmd_profile(
    settings: IngestSettings,
    survey: str | None,
    table: str | None,
    release: int | None = None,
) -> int:
    writer = get_writer(settings)
    targets = [
        (s, t)
        for s, t in writer.tables(survey)
        if table is None or t == table  # type: ignore[arg-type]
    ]
    for s, t in targets:
        prof = profile_table(writer, s, t)
        print(f"profiled  {s}/{t}: {prof.num_rows} columns")
    if not targets:
        print("no stored tables match")
        return 1
    for s in sorted({s for s, _ in targets}):
        year = release or loaded_release(writer, s)
        done = finish_profile(writer, s, year) if year is not None else None
        if done is None:
            print(
                f"waiting   {s}: undocumented flags and unmatched_codes need ref/codes"
            )
        else:
            print(
                f"finished  {s} {year}: {done[0]} undocumented columns, "
                f"{done[1]} values not in a code list"
            )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ce-ingest")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    files = sub.add_parser("files", help="dry run: list what would load")
    files.add_argument("--landing", type=Path)
    files.add_argument("--survey", choices=["intrvw", "diary"])

    load = sub.add_parser("load", help="load files into the output tables")
    load.add_argument("--landing", type=Path)
    load.add_argument("--out", type=Path)
    load.add_argument("--survey", choices=["intrvw", "diary"])
    load.add_argument("--year", type=int, help="release year, e.g. 2025")
    load.add_argument("--backend", choices=["parquet", "delta"])
    load.add_argument("--no-profile", action="store_true", help="skip profiling")

    prof = sub.add_parser("profile", help="profile stored tables")
    prof.add_argument("--out", type=Path)
    prof.add_argument("--survey", choices=["intrvw", "diary"])
    prof.add_argument("--table", help="one table, e.g. fmli or expn_rnt")
    prof.add_argument("--backend", choices=["parquet", "delta"])
    prof.add_argument("--release", type=int, help="release year; default: the latest")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    overrides = {
        key: value
        for key, value in {
            "landing_dir": getattr(args, "landing", None),
            "out_dir": getattr(args, "out", None),
            "backend": getattr(args, "backend", None),
        }.items()
        if value is not None
    }
    settings = IngestSettings(**overrides)

    if args.command == "files":
        return _cmd_files(settings, args.survey)
    if args.command == "load":
        return _cmd_load(settings, args.survey, args.year, not args.no_profile)
    if args.command == "profile":
        return _cmd_profile(settings, args.survey, args.table, args.release)
    return 2


if __name__ == "__main__":
    sys.exit(main())
