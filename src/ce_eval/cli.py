"""`ce-eval`: the evaluation of Plan 6.

ce-eval gold [--figures]      check every gold record with no model
ce-eval freeze [--figures]    write the frozen outcomes from the gold records
ce-eval run --driver ...      put the cases to a model and score it
ce-eval rescore RESULTS.json  score a results file again, with no model run
ce-eval report RESULTS.json   print a results file as a report
ce-eval judge-check --judge M check a judging model on labelled examples
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ce_eval.cases import CASES_DIR, SETS, load_cases


def _context(data: bool):
    """The real sidecar set. With `data`, the configured data folder too."""
    from ce_capture.context import CaptureContext
    from ce_mcp_server.service import build_service
    from ce_meta.config import MetaSettings

    settings = MetaSettings()
    ctx = CaptureContext.from_service(build_service(settings))
    return ctx, (settings.data_dir if data else None)


def _gold(args: argparse.Namespace):
    from ce_eval import offline

    cases = load_cases(args.cases, args.set)
    ctx, data_dir = _context(args.figures)
    env = offline.make_env(ctx, cases.loaded_years, data_dir)
    runs = offline.run_gold(env, cases, "run" if args.figures else "evaluate")
    return offline, cases, env, offline.check(runs, env.store)


def _cmd_gold(args: argparse.Namespace) -> int:
    offline, cases, env, runs = _gold(args)
    failed = 0
    for run in runs:
        if run.problems or args.verbose:
            mark = "FAIL" if run.problems else "ok  "
            print(
                f"{mark} {run.case.set}/{run.case.id}: {offline.snapshot(run)['kind']}"
            )
        for problem in run.problems:
            print(f"       {problem}")
        failed += bool(run.problems)
    whole = not args.set
    name = offline.FIGURES_FILE if args.figures else offline.VERDICTS_FILE
    doc = (offline.figures_doc if args.figures else offline.verdicts_doc)(env, runs)
    frozen = offline.read_frozen(args.cases, name)
    drift: list[str] = []
    if frozen is None:
        print(f"no frozen file {name}: run ce-eval freeze")
    elif whole:
        drift = offline.differences(doc, frozen)
        for line in drift:
            print(f"DIFF {line}")
    print(
        f"{len(runs)} cases, {failed} failed, "
        f"{len(drift)} differ from the frozen outcomes"
    )
    return 1 if failed or drift else 0


def _cmd_freeze(args: argparse.Namespace) -> int:
    offline, cases, env, runs = _gold(args)
    bad = [r for r in runs if r.problems]
    for run in bad:
        print(f"FAIL {run.case.id}: {run.problems}")
    if bad and not args.force:
        print("not frozen: fix the cases, or pass --force", file=sys.stderr)
        return 1
    name = offline.FIGURES_FILE if args.figures else offline.VERDICTS_FILE
    doc = (offline.figures_doc if args.figures else offline.verdicts_doc)(env, runs)
    print(f"wrote {offline.write_frozen(args.cases, name, doc)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ce-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--cases", type=Path, default=CASES_DIR)
        p.add_argument("--set", action="append", choices=SETS)

    gold = sub.add_parser("gold", help="check the gold records with no model")
    common(gold)
    gold.add_argument("--figures", action="store_true", help="compute on the data")
    gold.add_argument("-v", "--verbose", action="store_true")
    freeze = sub.add_parser("freeze", help="write the frozen outcomes")
    common(freeze)
    freeze.add_argument("--figures", action="store_true")
    freeze.add_argument("--force", action="store_true")

    from ce_eval import cli_run

    cli_run.add_parsers(sub, common)
    args = parser.parse_args(argv)
    if args.command == "gold":
        return _cmd_gold(args)
    if args.command == "freeze":
        return _cmd_freeze(args)
    return cli_run.dispatch(args)


if __name__ == "__main__":
    sys.exit(main())
