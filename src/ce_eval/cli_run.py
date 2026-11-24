"""The model-facing commands of `ce-eval`: `run` and `report`."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ce_eval.cases import load_cases

RESULTS_DIR = Path("eval/results")
DRIVERS = ("oracle", "claude-cli", "databricks")


def add_parsers(sub, common) -> None:
    run = sub.add_parser("run", help="put the cases to a model and score it")
    common(run)
    run.add_argument("--case", action="append", help="only these case IDs")
    run.add_argument("--driver", choices=DRIVERS, default="oracle")
    run.add_argument("--model", help="the model name the driver passes on")
    run.add_argument("--fault", action="append", default=[], help="oracle only")
    run.add_argument("--prompt", default="minimal", help="a file of eval/prompts")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument(
        "--no-data", action="store_true", help="judge only, compute nothing"
    )
    run.add_argument("--judge", help="a model that judges paraphrased cautions")
    run.add_argument("--judge-votes", type=int, default=3)
    run.add_argument("--without-tool", action="append", default=[], help="an ablation")
    run.add_argument("--max-lookups", type=int, help="an ablation; loop drivers only")
    run.add_argument("--out", type=Path, help="the results file")
    run.add_argument("--workers", type=int, default=1, help="conversations at once")
    rescore = sub.add_parser("rescore", help="score a results file again, no model")
    common(rescore)
    rescore.add_argument("results", type=Path)
    rescore.add_argument("--judge")
    rescore.add_argument("--judge-votes", type=int, default=3)
    rescore.add_argument("--no-data", action="store_true")
    rescore.add_argument("--workers", type=int, default=1, help="judging at once")
    rescore.add_argument("--out", type=Path, help="default: over the same file")
    check = sub.add_parser("judge-check", help="check a judge on labelled examples")
    check.add_argument("--judge", required=True, help="the judging model")
    check.add_argument("--votes", type=int, default=3)
    check.add_argument("--checks", type=Path, default=Path("eval/judge_checks.yaml"))
    report = sub.add_parser("report", help="print a results file as a report")
    report.add_argument("results", type=Path)
    report.add_argument("--cases", type=Path, default=Path("eval/cases"))
    report.add_argument("--marks", type=Path, default=Path("eval/pass_marks.yaml"))
    report.add_argument("--out", type=Path, help="write the Markdown here")


def _driver(args, cases, sidecar_dir: Path, data_dir: Path | None):
    from ce_eval import drivers, scripted

    prompt = drivers.load_prompt(args.prompt)
    if args.driver == "oracle":
        model = scripted.Oracle(cases, tuple(args.fault))
        return drivers.LoopDriver(
            model, prompt, tuple(args.without_tool), args.max_lookups
        )
    if args.driver == "databricks":
        from ce_capture.config import CaptureSettings
        from ce_capture.databricks import DatabricksEndpoint

        settings = CaptureSettings()
        endpoint = args.model or settings.endpoint
        if not endpoint:
            raise SystemExit("no endpoint: pass --model or set CE_CAPTURE_ENDPOINT")
        model = DatabricksEndpoint(endpoint, settings)
        return drivers.LoopDriver(
            model, prompt, tuple(args.without_tool), args.max_lookups
        )
    paths = drivers.ServerPaths(sidecar_dir, data_dir)
    return drivers.ClaudeCliDriver(
        args.model or "sonnet", prompt, paths, tuple(args.without_tool)
    )


def _cmd_run(args: argparse.Namespace) -> int:
    from ce_capture.context import CaptureContext
    from ce_eval import harness, judge, report
    from ce_mcp_server.service import _locate, build_service
    from ce_meta.config import MetaSettings

    cases = load_cases(args.cases, args.set)
    if args.case:
        cases = cases.select(ids=args.case)
    settings = MetaSettings()
    ctx = CaptureContext.from_service(build_service(settings))
    data_dir = None if args.no_data else _locate(settings.data_dir)
    driver = _driver(args, cases, _locate(settings.sidecar_dir), data_dir)
    referee = (
        judge.claude_cli_judge(args.judge, votes=args.judge_votes)
        if args.judge
        else None
    )

    def progress(score) -> None:
        mark = "FAIL " + ",".join(score.failures) if score.failures else "ok"
        print(f"  {score.set}/{score.case} [{score.run}]: {mark}", flush=True)

    started = time.time()
    name = driver.name.replace(":", "-").replace("/", "-")
    out = args.out or RESULTS_DIR / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    results = harness.run_model(
        ctx, cases, driver, data_dir=data_dir, repeat=args.repeat, judge=referee,
        records=out.with_suffix(".records"), workers=args.workers,
        partial=out.with_suffix(".partial.jsonl"), progress=progress,
    )  # fmt: skip
    results["prompt"] = args.prompt
    results["seconds"] = round(time.time() - started, 1)
    results["without_tools"] = args.without_tool
    summary = report.summarize(results, report.load_marks())
    results["summary"] = summary
    out.write_text(json.dumps(results, indent=1, default=str))
    print(report.markdown(results, summary, cases.nuances))
    print(f"wrote {out}", file=sys.stderr)
    unmet = [m for m in summary["marks"] if m["met"] is False]
    return 1 if unmet else 0


def _cmd_report(args: argparse.Namespace) -> int:
    from ce_eval import report
    from ce_eval.cases import load_nuances

    results = json.loads(args.results.read_text())
    summary = report.summarize(results, report.load_marks(args.marks))
    text = report.markdown(results, summary, load_nuances(args.cases))
    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


def _cmd_rescore(args: argparse.Namespace) -> int:
    from ce_capture.context import CaptureContext
    from ce_eval import harness, judge, report
    from ce_mcp_server.service import _locate, build_service
    from ce_meta.config import MetaSettings

    cases = load_cases(args.cases, args.set)
    settings = MetaSettings()
    ctx = CaptureContext.from_service(build_service(settings))
    data_dir = None if args.no_data else _locate(settings.data_dir)
    referee = (
        judge.claude_cli_judge(args.judge, votes=args.judge_votes)
        if args.judge
        else None
    )
    results = harness.rescore(
        json.loads(args.results.read_text()),
        ctx,
        cases,
        data_dir=data_dir,
        judge=referee,
        workers=args.workers,
    )
    summary = report.summarize(results, report.load_marks())
    results["summary"] = summary
    out = args.out or args.results
    out.write_text(json.dumps(results, indent=1, default=str))
    print(report.markdown(results, summary, cases.nuances))
    print(f"wrote {out}", file=sys.stderr)
    return 0


def _cmd_judge_check(args: argparse.Namespace) -> int:
    from ce_eval import judge

    referee = judge.claude_cli_judge(args.judge, votes=args.votes)
    found = judge.check_judge(referee, judge.load_checks(args.checks))
    for row in found:
        mark = "ok  " if row["agrees"] else "WRONG"
        print(
            f"{mark} {row['kind']:9s} {row['id']}: "
            f"said {row['got']}, is {row['expect']}"
        )
    wrong = sum(1 for row in found if not row["agrees"])
    print(f"{len(found)} examples, {wrong} wrong")
    return 1 if wrong else 0


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "rescore":
        return _cmd_rescore(args)
    if args.command == "report":
        return _cmd_report(args)
    if args.command == "judge-check":
        return _cmd_judge_check(args)
    return 2
