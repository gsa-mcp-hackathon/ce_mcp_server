"""The gold records, run with no model (Plan 6, "How it runs", run 1).

Every gold record goes through capture and processing exactly as a model's
would: `submit` (normalize, check, derive), then `evaluate`, or `run` when the
data is loaded. Three things are checked:

- the case's own expectations (`expect`): the capture status, flags and
  resolved choices, and the verdicts and rules of its outcome;
- the frozen verdicts (`frozen/verdicts.json`): the whole outcome of every
  case, so a change to a rule, a default or a sidecar that moves any gold case
  shows up as a difference, with no model run;
- the frozen figures (`frozen/figures.json`), when the data is loaded.

The frozen files are written by code (`ce-eval freeze`), never by hand.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ce_capture.context import CaptureContext
from ce_capture.gold import expectations
from ce_capture.pipeline import CapturePipeline, Submission
from ce_capture.storage import JsonFileStore
from ce_eval.cases import Case, CaseSet
from ce_eval.signature import answered
from ce_measures.errors import CeMeasuresError, NotProcessable
from ce_measures.outcome import RULES_VERSION, dump
from ce_measures.process import RequestProcessor
from ce_meta.catalog import SURVEY
from ce_store import survey_year_set

FROZEN_DIR = "frozen"
VERDICTS_FILE = "verdicts.json"
FIGURES_FILE = "figures.json"
TOLERANCE = 1e-9


@dataclass
class Env:
    """A capture pipeline and a processor on one store."""

    ctx: CaptureContext
    store: JsonFileStore
    data_dir: Path | None
    pipeline: CapturePipeline
    processor: RequestProcessor


def make_env(
    ctx: CaptureContext,
    loaded_years: tuple[int, ...] | list[int],
    data_dir: Path | None = None,
    records_dir: Path | None = None,
) -> Env:
    """An environment in which exactly `loaded_years` count as loaded, whatever
    the registry says, so a case set gives the same verdicts everywhere."""
    partitions = {p for y in loaded_years for p in survey_year_set(SURVEY, y)}
    ctx = dataclasses.replace(ctx, loaded_partitions=partitions)
    store = JsonFileStore(records_dir or Path(tempfile.mkdtemp(prefix="ce-eval-")))
    return Env(
        ctx,
        store,
        data_dir,
        CapturePipeline(ctx, store),
        RequestProcessor(ctx, store, data_dir),
    )


@dataclass
class GoldRun:
    case: Case
    reply: dict  # what submit returned
    request: dict | None = None
    resolved: dict | None = None
    outcome: dict | None = None  # the outcome record, when the request was captured
    problems: list[str] = field(default_factory=list)
    alternatives: list[GoldRun] = field(default_factory=list)  # other right records

    @property
    def status(self) -> str | None:
        return self.reply.get("status")

    @property
    def request_id(self) -> str | None:
        return self.reply.get("request_id")


def expected_kind(
    status: str | None, request: dict | None, outcome: dict | None
) -> str:
    """What the user should receive: figures, a block's reason, a question, a
    plain decline, or (mixed) figures for some phrases and reasons for others."""
    if status == "needs_clarification":
        return "question"
    if status == "not_captured":
        return "decline"
    if status != "captured" or outcome is None:
        return "none"
    verdicts = {n["id"]: n["verdict"] for n in outcome["nodes"]}
    nodes = [a["node"] for a in (request or {}).get("answers") or []] or list(verdicts)
    blocked = [verdicts.get(n) == "block" for n in nodes]
    if all(blocked):
        return "block"
    return "mixed" if any(blocked) else "figures"


def process(env: Env, request_id: str, mode: str) -> dict | None:
    """The outcome record of a captured request, or None when it is not processed."""
    try:
        fn = env.processor.run if mode == "run" else env.processor.evaluate
        record, _ = fn(request_id)
    except NotProcessable:
        return None
    return dump(record)


def run_gold(env: Env, cases: CaseSet, mode: str = "evaluate") -> list[GoldRun]:
    """Submit every gold record, conversation by conversation, and process it."""
    ids: dict[str, str] = {}
    runs: list[GoldRun] = []
    for case in cases.cases:
        sub = Submission(
            user_text=case.message,
            session_id=f"gold-{cases.root(case).id}",
            output=case.gold,
            follows_request_id=ids.get(case.follows or ""),
            answers_request_id=ids.get(case.answers or ""),
            corrects_request_id=ids.get(case.corrects or ""),
        )
        try:
            reply = env.pipeline.submit(sub)
        except Exception as exc:  # a link refused: the case is wrong, say why
            runs.append(GoldRun(case, {"status": "link_failed"}, problems=[str(exc)]))
            continue
        ids[case.id] = reply["request_id"]
        run = GoldRun(case, reply)
        run.request = env.store.read("request", reply["request_id"])
        if reply.get("resolved_id"):
            run.resolved = env.store.read("resolved", reply["resolved_id"])
        if reply.get("status") == "captured":
            try:
                run.outcome = process(env, reply["request_id"], mode)
            except CeMeasuresError as exc:
                run.problems.append(f"processing failed: {exc.message}")
        if case.raw.get("also"):
            if case.link:
                run.problems.append("`also` is for a case that links to no other")
            for i, record in enumerate(case.alternatives()):
                run.alternatives.append(_alternative(env, case, record, i, mode))
        runs.append(run)
    return runs


def _alternative(env: Env, case: Case, record: dict, i: int, mode: str) -> GoldRun:
    """An alternative gold record, submitted in a session of its own."""
    sub = Submission(case.message, f"gold-{case.id}-also-{i}", record)
    reply = env.pipeline.submit(sub)
    run = GoldRun(case, reply)
    run.request = env.store.read("request", reply["request_id"])
    if reply.get("resolved_id"):
        run.resolved = env.store.read("resolved", reply["resolved_id"])
    if reply.get("status") == "captured":
        run.outcome = process(env, reply["request_id"], mode)
    return run


# ── a case's own expectations ────────────────────────────────────────────────


def outcome_problems(expect: dict, outcome: dict | None) -> list[str]:
    """`expect.outcome` against an outcome record: the worst verdict, and per
    node its verdict, method, and the rules that must and must not fire."""
    want = expect.get("outcome")
    if want is None:
        return []
    if outcome is None:
        return ["no outcome record: the request was not captured"]
    problems = []
    nodes = {n["id"]: n for n in outcome["nodes"]}
    for key, value in want.items():
        if key == "verdict":
            if outcome["verdict"] != value:
                problems.append(f"outcome is {outcome['verdict']}, expected {value}")
            continue
        node = nodes.get(key)
        if node is None:
            problems.append(f"no outcome for node {key}")
            continue
        fired = [m["rule"] for m in node["messages"]]
        for name in ("verdict", "method", "weighted"):
            if name in value and node.get(name) != value[name]:
                problems.append(
                    f"{key}.{name} is {node.get(name)!r}, expected {value[name]!r}"
                )
        for rule in value.get("rules") or []:
            if rule not in fired:
                problems.append(f"{key}: rule {rule} did not fire; fired {fired}")
        for rule in value.get("not_rules") or []:
            if rule in fired:
                problems.append(f"{key}: rule {rule} fired and should not")
    return problems


def check(runs: list[GoldRun], store: JsonFileStore) -> list[GoldRun]:
    """Fill each run's problems from its case's expectations."""
    by_id = {r.case.id: r for r in runs}
    for run in runs:
        if run.status == "link_failed":
            continue
        run.problems += expectations(run.case.as_gold(), run.reply, store)
        run.problems += outcome_problems(run.case.expect, run.outcome)
        for i, other in enumerate(run.alternatives):
            if other.status != run.status:
                run.problems.append(
                    f"alternative {i} is {other.status}, not {run.status}: "
                    f"{other.reply.get('errors')}"
                )
            elif snapshot(other).get("answered") == snapshot(run).get("answered"):
                run.problems.append(f"alternative {i} computes what the gold does")
        kind = run.case.expect.get("kind")
        got = expected_kind(run.status, run.request, run.outcome)
        if kind and kind != got:
            run.problems.append(f"the answer kind is {got}, expected {kind}")
        base = by_id.get(run.case.rewords or "")
        if base is not None and snapshot(run)["answered"] != snapshot(base)["answered"]:
            run.problems.append(
                f"computes something other than {base.case.id}, which it rewords"
            )
    return runs


# ── frozen outcomes ──────────────────────────────────────────────────────────


def snapshot(run: GoldRun) -> dict:
    """What code makes of one gold record, without figures."""
    out: dict = {
        "status": run.status,
        "kind": expected_kind(run.status, run.request, run.outcome),
    }
    if run.reply.get("questions"):
        out["questions"] = run.reply["questions"]
    if run.request is not None and run.status == "captured":
        out["answered"] = answered(run.request, run.resolved)
    if run.outcome is not None:
        out["verdict"] = run.outcome["verdict"]
        out["nodes"] = {
            n["id"]: {
                "verdict": n["verdict"],
                "method": n.get("method"),
                "weighted": n["weighted"],
                "rules": [m["rule"] for m in n["messages"]],
            }
            for n in run.outcome["nodes"]
        }
    return out


def versions(env: Env) -> dict:
    master = env.ctx.sidecars.master
    vocab = env.ctx.sidecars.vocabulary
    return {
        "rules_version": RULES_VERSION,
        "master": master.version if master else None,
        "vocabulary": vocab.version if vocab else None,
    }


def verdicts_doc(env: Env, runs: list[GoldRun]) -> dict:
    return {
        "versions": versions(env),
        "cases": {r.case.id: snapshot(r) for r in runs},
    }


def figures_doc(env: Env, runs: list[GoldRun]) -> dict:
    cases = {}
    for run in runs:
        if run.outcome is None:
            continue
        nodes = {
            n["id"]: [
                {
                    "group": g["group"],
                    "value": g["value"],
                    "standard_error": g.get("standard_error"),
                }
                for g in n["result"]["groups"]
            ]
            for n in run.outcome["nodes"]
            if n.get("result")
        }
        if nodes:
            cases[run.case.id] = nodes
    return {"versions": versions(env), "cases": cases}


def _differs(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is not b
        return abs(a - b) > TOLERANCE * max(1.0, abs(a), abs(b))
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) != set(b) or any(_differs(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) != len(b) or any(
            _differs(x, y) for x, y in zip(a, b, strict=True)
        )
    return a != b


def differences(current: dict, frozen: dict) -> list[str]:
    """Where the outcomes now differ from the frozen ones, case by case."""
    problems = []
    if current.get("versions") != frozen.get("versions"):
        problems.append(
            f"versions changed: frozen {frozen.get('versions')}, "
            f"now {current.get('versions')}"
        )
    now, then = current.get("cases") or {}, frozen.get("cases") or {}
    for case in sorted(set(now) | set(then)):
        if case not in then:
            problems.append(f"{case}: not in the frozen file")
        elif case not in now:
            problems.append(f"{case}: in the frozen file, no longer a case")
        elif _differs(now[case], then[case]):
            keys = (
                sorted(
                    k
                    for k in set(now[case]) | set(then[case])
                    if _differs(now[case].get(k), then[case].get(k))
                )
                if isinstance(now[case], dict)
                else []
            )
            problems.append(f"{case}: differs from the frozen outcome in {keys}")
    return problems


def frozen_path(folder: Path, name: str) -> Path:
    return Path(folder) / FROZEN_DIR / name


def read_frozen(folder: Path, name: str) -> dict | None:
    path = frozen_path(folder, name)
    return json.loads(path.read_text()) if path.exists() else None


def write_frozen(folder: Path, name: str, doc: dict) -> Path:
    path = frozen_path(folder, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return path
