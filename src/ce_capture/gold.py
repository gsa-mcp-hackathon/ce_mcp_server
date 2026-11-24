"""The gold set: checking it offline, and scoring a model against it (step 10).

`check_gold` submits every gold record on the client-model path and compares
what code made of it with the case's expectations; it needs no model. `score`
runs the messages through the server-side model and compares the model's
records with the gold ones field by field, with the stated choices scored
apart, and counts lookups and tokens.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ce_capture.agent import ServerCapture
from ce_capture.context import CaptureContext
from ce_capture.model import ChatReply, ModelClient, ModelError
from ce_capture.pipeline import CapturePipeline, LinkError, Submission
from ce_capture.storage import JsonFileStore

DEFAULT_GOLD = Path("eval/cases/core.yaml")


def load_gold(path: Path = DEFAULT_GOLD) -> list[dict]:
    return yaml.safe_load(Path(path).read_text())["cases"]


def _value(entry):
    if isinstance(entry, dict) and "source" in entry:
        return entry.get("value", entry.get("result"))
    return entry


# ── offline check of the gold set ────────────────────────────────────────────


@dataclass
class GoldResult:
    case: str
    request_id: str | None
    problems: list[str] = field(default_factory=list)


def _expectations(case: dict, reply: dict, store: JsonFileStore) -> list[str]:
    expect = case.get("expect") or {}
    problems = []
    if reply.get("status") != expect.get("status"):
        problems.append(
            f"status {reply.get('status')}, expected {expect.get('status')}; "
            f"errors: {reply.get('errors')}"
        )
    flags = " ".join(reply.get("flags") or [])
    for code in expect.get("flags") or []:
        if code not in flags:
            problems.append(f"no {code} flag; flags: {reply.get('flags')}")
    for code in expect.get("not_flags") or []:
        if code in flags:
            problems.append(f"unexpected {code} flag; flags: {reply.get('flags')}")
    rules = {f["rule"] for f in reply.get("fixes") or []}
    for rule in expect.get("fixes") or []:
        if rule not in rules:
            problems.append(f"no {rule} fix; fixes: {reply.get('fixes')}")
    for q in expect.get("questions") or []:
        if q not in (reply.get("questions") or []):
            problems.append(
                f"question {q!r} not asked; questions: {reply.get('questions')}"
            )
    if expect.get("resolved") or expect.get("resolved_flags"):
        resolved = store.read("resolved", reply.get("resolved_id") or "")
        if resolved is None:
            return problems + ["no resolved record"]
        nodes = {n["id"]: n for n in resolved["nodes"]}
        for nid, fields in (expect.get("resolved") or {}).items():
            for key, want in fields.items():
                got = _value((nodes.get(nid) or {}).get(key))
                if got != want:
                    problems.append(f"{nid}.{key} is {got!r}, expected {want!r}")
        joined = " ".join(resolved["flags"])
        for code in expect.get("resolved_flags") or []:
            if code not in joined:
                problems.append(f"no resolved flag {code}; flags: {resolved['flags']}")
    return problems


expectations = _expectations  # what `ce_eval` checks a capture reply against


def check_gold(ctx: CaptureContext, cases: list[dict]) -> list[GoldResult]:
    """Submit each gold record on the client-model path; report what differs."""
    store = JsonFileStore(Path(tempfile.mkdtemp(prefix="ce-gold-")))
    pipeline = CapturePipeline(ctx, store)
    ids: dict[str, str] = {}
    results = []
    for case in cases:
        sub = Submission(
            user_text=case["message"],
            session_id=f"gold-{_root(case, cases)}",
            output=case["gold"],
            follows_request_id=ids.get(case.get("follows", "")),
            answers_request_id=ids.get(case.get("answers", "")),
        )
        reply = pipeline.submit(sub)
        ids[case["id"]] = reply["request_id"]
        results.append(
            GoldResult(
                case["id"], reply["request_id"], _expectations(case, reply, store)
            )
        )
    return results


def _root(case: dict, cases: list[dict]) -> str:
    """Linked cases share a session: the one of the case they start from."""
    by_id = {c["id"]: c for c in cases}
    while case.get("follows") or case.get("answers"):
        case = by_id[case.get("follows") or case.get("answers")]
    return case["id"]


# ── scoring a model ──────────────────────────────────────────────────────────

FIELDS = ("status", "statistic", "measure", "group_by", "filters", "period")


class _Counting:
    """Wraps a model client to count tokens."""

    def __init__(self, model: ModelClient) -> None:
        self.model = model
        self.name = model.name
        self.tokens = Counter()

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatReply:
        reply = self.model.chat(messages, tools)
        for k, v in (reply.usage or {}).items():
            if isinstance(v, int):
                self.tokens[k] += v
        return reply


def _canon(node: dict) -> dict:
    refs = lambda rs: sorted((r["table"], r["column"]) for r in rs or [])  # noqa: E731
    filters = sorted(
        (
            f["table"],
            f["column"],
            f["comparison"],
            json.dumps(f["value"], sort_keys=True),
        )
        for f in node.get("filters") or []
    )
    period = node.get("period") or {}
    measure = node.get("measure")
    return {
        "statistic": node.get("statistic"),
        "measure": (measure["table"], measure["column"]) if measure else None,
        "group_by": refs(node.get("group_by")),
        "filters": filters,
        "period": (period.get("kind"), list(period.get("values") or [])),
        "stated": {k: v["value"] for k, v in (node.get("stated") or {}).items() if v},
    }


def compare(got: dict, gold: dict) -> dict[str, float]:
    """Field scores for one case, matching nodes by their statistic and measure."""
    scores: dict[str, float] = {"status": float(got["status"] == gold["status"])}
    gold_nodes = [_canon(n) for n in gold["nodes"] if n.get("kind") == "estimate"]
    got_nodes = [_canon(n) for n in got["nodes"] if n.get("kind") == "estimate"]
    if not gold_nodes:
        scores["nodes"] = float(not got_nodes)
        return scores
    pool = list(got_nodes)
    totals: Counter = Counter()
    for g in gold_nodes:
        match = next(
            (
                n
                for n in pool
                if (n["statistic"], n["measure"]) == (g["statistic"], g["measure"])
            ),
            pool[0] if pool else None,
        )
        if match is not None:
            pool.remove(match)
        for key in ("statistic", "measure", "group_by", "filters", "period"):
            totals[key] += float(match is not None and match[key] == g[key])
        for key, want in g["stated"].items():
            totals["stated"] += 1
            totals["stated_right"] += float(
                match is not None and match["stated"].get(key) == want
            )
        extra = set(match["stated"]) - set(g["stated"]) if match else set()
        totals["stated_extra"] += len(extra)
    n = len(gold_nodes)
    for key in ("statistic", "measure", "group_by", "filters", "period"):
        scores[key] = totals[key] / n
    if totals["stated"]:
        scores["stated"] = totals["stated_right"] / totals["stated"]
    scores["stated_extra"] = totals["stated_extra"]
    scores["nodes"] = float(len(got_nodes) == len(gold_nodes))
    return scores


OutcomeScore = Callable[[dict, dict | None, dict, dict | None], float]


def _resolved(store: JsonFileStore, request_id: str) -> dict | None:
    found = store.resolved_for(request_id)
    return found[0] if found else None


def score(
    ctx: CaptureContext,
    model: ModelClient,
    cases: list[dict],
    repeat: int = 1,
    outcome: OutcomeScore | None = None,
) -> dict:
    """Run every message through the server-side model `repeat` times.

    The model's records are compared with the gold records, which are made by
    submitting each gold output on the client-model path. A follow-up or an
    answer links to the model's own earlier record in that run, so the score
    reflects a whole conversation; when that link cannot be made, the case
    scores zero with the status `link_failed`. A case on which the model cannot
    be reached scores zero with the status `model_failed`, and the run goes on.

    `outcome` scores the outcome level: given the model's request and resolved
    records and the gold's, whether they compute the same statistics. Two
    records can differ field by field and still be right, so this is the score
    that says whether the user would get the right answer (Plan 6). The
    evaluation package supplies it (`ce_eval.signature.outcome_score`).
    """
    gold_store = JsonFileStore(Path(tempfile.mkdtemp(prefix="ce-score-gold-")))
    gold_pipeline = CapturePipeline(ctx, gold_store)
    gold_records: dict[str, dict] = {}
    gold_resolved: dict[str, dict | None] = {}
    gold_ids: dict[str, str] = {}
    for case in cases:
        reply = gold_pipeline.submit(
            Submission(
                case["message"],
                f"gold-{_root(case, cases)}",
                case["gold"],
                follows_request_id=gold_ids.get(case.get("follows", "")),
                answers_request_id=gold_ids.get(case.get("answers", "")),
            )
        )
        gold_ids[case["id"]] = reply["request_id"]
        gold_records[case["id"]] = gold_store.read("request", reply["request_id"])
        gold_resolved[case["id"]] = _resolved(gold_store, reply["request_id"])

    runs = []
    for i in range(repeat):
        store = JsonFileStore(Path(tempfile.mkdtemp(prefix=f"ce-score-{i}-")))
        pipeline = CapturePipeline(ctx, store)
        model_ids: dict[str, str] = {}
        for case in cases:
            counting = _Counting(model)
            agent = ServerCapture(pipeline, counting)
            started = time.time()
            gold = gold_records[case["id"]]
            gold_view = gold | {"status": gold["capture"]["status"]}
            try:
                reply = agent.capture(
                    case["message"],
                    f"score-{i}-{_root(case, cases)}",
                    follows_request_id=model_ids.get(case.get("follows", "")),
                    answers_request_id=model_ids.get(case.get("answers", "")),
                )
            except (LinkError, ModelError) as exc:
                failed = "link_failed" if isinstance(exc, LinkError) else "model_failed"
                runs.append(
                    {"case": case["id"], "run": i, "status": failed,
                     "error": str(exc), "scores": {"status": 0.0}}
                )  # fmt: skip
                continue
            model_ids[case["id"]] = reply["request_id"]
            record = store.read("request", reply["request_id"])
            got = record | {"status": record["capture"]["status"]}
            scores = compare(got, gold_view)
            if outcome is not None:
                scores["outcome"] = outcome(
                    record,
                    _resolved(store, reply["request_id"]),
                    gold,
                    gold_resolved[case["id"]],
                )
            runs.append(
                {
                    "case": case["id"],
                    "run": i,
                    "status": got["status"],
                    "scores": scores,
                    "lookups": record["capture"].get("lookups"),
                    "attempts": record["capture"]["attempts"],
                    "tokens": dict(counting.tokens),
                    "seconds": round(time.time() - started, 1),
                }
            )
    return {
        "model": model.name,
        "repeat": repeat,
        "runs": runs,
        "summary": summarize(runs),
    }


def summarize(runs: list[dict]) -> dict:
    by_field: dict[str, list[float]] = {}
    for run in runs:
        for key, value in run["scores"].items():
            by_field.setdefault(key, []).append(value)
    summary = {k: round(sum(v) / len(v), 3) for k, v in by_field.items() if v}
    by_case: dict[str, set[str]] = {}
    for run in runs:
        by_case.setdefault(run["case"], set()).add(run["status"])
    summary["consistent_cases"] = sum(1 for s in by_case.values() if len(s) == 1)
    summary["cases"] = len(by_case)
    lookups = [r["lookups"] for r in runs if r.get("lookups") is not None]
    summary["mean_lookups"] = round(sum(lookups) / len(lookups), 1) if lookups else None
    return summary
