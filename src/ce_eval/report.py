"""Reading a results document: the measures, the pass marks, and the failures
by set and by nuance, so a weak spot shows as a row (Plan 6)."""

from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path

import yaml

from ce_eval.cases import SETS
from ce_eval.harness import LIKELY_FIX

PASS_MARKS = Path("eval/pass_marks.yaml")
RATES = (
    "silent_wrong_rate", "status_accuracy", "outcome_equivalence", "asked_recall",
    "first_pass", "caution_retention", "caution_verbatim", "number_fidelity",
    "block_honesty", "question_asked", "tool_order", "missed_rate", "consistency",
    "worked_around_rate",
)  # fmt: skip


def _mean(values: list) -> float | None:
    values = [float(v) for v in values if v is not None]
    return sum(values) / len(values) if values else None


def consistency(scores: list[dict]) -> float | None:
    """The share of cases whose status and computed statistics are the same in
    every run; None with a single run."""
    by_case: dict[str, set[str]] = {}
    runs = {s["run"] for s in scores}
    if len(runs) < 2:
        return None
    for s in scores:
        by_case.setdefault(s["case"], set()).add(s["signature"])
    return sum(1 for v in by_case.values() if len(v) == 1) / len(by_case)


def measures(scores: list[dict]) -> dict:
    """The measures of Plan 6 over some turn scores."""
    if not scores:
        return {"turns": 0}
    counted = [s for s in scores if not s["cautions_optional"]]
    total = sum(s["cautions"].get("total", 0) for s in counted)
    verbatim = sum(s["cautions"].get("verbatim", 0) for s in counted)
    para = sum(s["cautions"].get("paraphrase", 0) for s in counted)
    judged = sum(s["cautions"].get("judged", 0) for s in counted)
    lookups = [s["lookups"] for s in scores if s["attempts"]]
    return {
        "turns": len(scores),
        "cases": len({s["case"] for s in scores}),
        "silent_wrong_rate": _mean([s["silent_wrong"] for s in scores]),
        "worked_around_rate": _mean([s.get("worked_around", False) for s in scores]),
        "status_accuracy": _mean([s["status_ok"] for s in scores]),
        "outcome_equivalence": _mean([s["outcome"] for s in scores]),
        "fabricated_stated": sum(s["stated_extra"] for s in scores),
        "asked_recall": _mean([s["asked_ok"] for s in scores]),
        "first_pass": _mean([s["first_pass"] for s in scores]),
        "mean_attempts": _mean([s["attempts"] for s in scores if s["attempts"]]),
        "caution_retention": (verbatim + para) / total if total else None,
        "caution_verbatim": verbatim / total if total else None,
        "cautions": total,
        "cautions_paraphrased_unjudged": max(para - judged, 0),
        "number_fidelity": _mean([s["faithful_numbers"] for s in scores]),
        "block_honesty": _mean([s["block_honest"] for s in scores]),
        "question_asked": _mean([s["question_asked"] for s in scores]),
        "tool_order": _mean([s["order_ok"] for s in scores]),
        "guide_first": _mean([s["guide_first"] for s in scores]),
        "missed_rate": _mean([s["missed"] for s in scores]),
        "consistency": consistency(scores),
        "median_lookups": statistics.median(lookups) if lookups else None,
        "mean_tokens": _mean([s["tokens"] for s in scores]),
        "mean_seconds": _mean([s["seconds"] for s in scores]),
        "errors": sum(1 for s in scores if s["error"]),
        "failed_turns": sum(1 for s in scores if s["failures"]),
    }


def by_set(scores: list[dict]) -> dict[str, dict]:
    return {
        name: measures([s for s in scores if s["set"] == name])
        for name in SETS
        if any(s["set"] == name for s in scores)
    }


def by_nuance(scores: list[dict]) -> dict[str, dict]:
    names = sorted({n for s in scores for n in s["nuance"]})
    return {n: measures([s for s in scores if n in s["nuance"]]) for n in names}


def load_marks(path: Path = PASS_MARKS) -> list[dict]:
    return (yaml.safe_load(Path(path).read_text()) or {}).get("marks") or []


def check_marks(scores: list[dict], marks: list[dict]) -> list[dict]:
    """Each pass mark with the value measured and whether it is met. A measure
    that could not be taken (no repeats, no such cases) is not judged."""
    out = []
    for mark in marks:
        scope = mark.get("scope", "all")
        subset = scores if scope == "all" else [s for s in scores if s["set"] == scope]
        value = measures(subset).get(mark["metric"])
        met = None
        if value is not None:
            met = (value >= mark["min"]) if "min" in mark else (value <= mark["max"])
        out.append({**mark, "value": value, "met": met})
    return out


def summarize(results: dict, marks: list[dict] | None = None) -> dict:
    scores = results["scores"]
    tags = Counter(tag for s in scores for tag in s["failures"])
    return {
        "model": results.get("model"),
        "judge": results.get("judge"),
        "repeat": results.get("repeat"),
        "mode": results.get("mode"),
        "overall": measures(scores),
        "by_set": by_set(scores),
        "by_nuance": by_nuance(scores),
        "failures": dict(tags.most_common()),
        "marks": check_marks(scores, marks or []),
    }


# ── as text ──────────────────────────────────────────────────────────────────


def _fmt(metric: str, value) -> str:
    if value is None:
        return "n/a"
    if metric in RATES or metric == "guide_first":
        return f"{100 * value:.0f}%"
    if isinstance(value, float):
        return f"{value:,.1f}"
    return f"{value:,}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return lines


COLUMNS = (
    ("turns", "Turns"),
    ("silent_wrong_rate", "Silent wrong"),
    ("status_accuracy", "Status"),
    ("outcome_equivalence", "Outcome"),
    ("number_fidelity", "Numbers"),
    ("caution_retention", "Cautions"),
    ("block_honesty", "Blocks"),
    ("tool_order", "Tool order"),
    ("failed_turns", "Failed turns"),
)


def markdown(results: dict, summary: dict, nuances: dict | None = None) -> str:
    """The report of one run, as Markdown."""
    overall = summary["overall"]
    lines = [
        f"# Evaluation of {summary['model']}",
        "",
        f"{overall.get('cases', 0)} cases, {overall['turns']} scored turns, "
        f"{summary['repeat']} run(s) of each; figures "
        f"{'computed on the data' if summary['mode'] == 'run' else 'not computed'}; "
        f"judge: {summary['judge'] or 'none'}.",
        "",
        "## Pass marks",
        "",
    ]
    rows = []
    for m in summary["marks"]:
        bound = f"at least {_fmt(m['metric'], m['min'])}" if "min" in m else (
            f"at most {_fmt(m['metric'], m['max'])}")  # fmt: skip
        verdict = {True: "met", False: "NOT MET", None: "not measured"}[m["met"]]
        rows.append(
            [m["metric"], m["scope"], _fmt(m["metric"], m["value"]), bound, verdict]
        )
    lines += _table(["Measure", "Scope", "Measured", "Pass mark", "Result"], rows)
    lines += ["", "## By set", ""]
    header = ["Set"] + [title for _, title in COLUMNS]
    rows = [
        [name] + [_fmt(key, m.get(key)) for key, _ in COLUMNS]
        for name, m in {**summary["by_set"], "all": overall}.items()
    ]
    lines += _table(header, rows)
    lines += ["", "## By nuance", ""]
    rows = []
    for name, m in sorted(
        summary["by_nuance"].items(), key=lambda kv: -(kv[1].get("failed_turns") or 0)
    ):
        title = nuances[name].title if nuances and name in nuances else name
        rows.append([title] + [_fmt(key, m.get(key)) for key, _ in COLUMNS])
    lines += _table(["Nuance"] + [title for _, title in COLUMNS], rows)
    lines += ["", "## Other measures", ""]
    extra = [
        ("worked_around_rate", "Figures not what was asked, said openly"),
        ("fabricated_stated", "Stated choices the user never made"),
        ("asked_recall", "Ambiguities and unresolved phrases raised"),
        ("question_asked", "Questions put to the user as written"),
        ("first_pass", "Records that passed the check first time"),
        ("mean_attempts", "Submissions per turn"),
        ("caution_verbatim", "Cautions kept word for word"),
        ("cautions_paraphrased_unjudged", "Cautions in other words, not judged"),
        ("missed_rate", "Answers withheld that should have held figures"),
        ("guide_first", "Guide read before the first record"),
        ("consistency", "Cases with the same outcome in every run"),
        ("median_lookups", "Lookups per turn (median)"),
        ("mean_tokens", "Tokens per turn"),
        ("mean_seconds", "Seconds per turn"),
        ("errors", "Turns the driver could not finish"),
    ]
    lines += _table(
        ["Measure", "Value"],
        [[title, _fmt(key, overall.get(key))] for key, title in extra],
    )
    failed = [s for s in results["scores"] if s["failures"]]
    cases = failed_cases(results["scores"])
    lines += [
        "",
        f"## Failed turns ({len(failed)}, in {len(cases)} cases)",
        "",
    ]
    if cases:
        runs = summary["repeat"]
        rows = []
        for c in cases:
            fixes = sorted({LIKELY_FIX[t] for t in c["failures"] if t in LIKELY_FIX})
            tags = ", ".join(
                f"{tag} ({n})" if runs > 1 and n < runs else tag
                for tag, n in c["failures"].items()
            )
            rows.append(
                [
                    c["case"],
                    c["set"],
                    ", ".join(c["nuance"]),
                    f"{c['failed']} of {runs}" if runs > 1 else "",
                    tags,
                    f"{c['gold_kind']} → {', '.join(c['kinds'])}",
                    "; ".join(fixes),
                ]
            )
        header = ["Case", "Set", "Nuance", "Runs failed", "What failed"]
        header += ["Expected → got", "Likely fix"]
        if runs == 1:
            rows = [r[:3] + r[4:] for r in rows]
            header.remove("Runs failed")
        lines += _table(header, rows)
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def failed_cases(scores: list[dict]) -> list[dict]:
    """The cases with a failed turn, each with how many of its runs failed and
    how often each kind of failure occurred, in the order of the cases."""
    out: dict[str, dict] = {}
    for s in scores:
        if not s["failures"]:
            continue
        entry = out.setdefault(
            s["case"],
            {"case": s["case"], "set": s["set"], "nuance": s["nuance"], "failed": 0,
             "failures": {}, "gold_kind": s["gold_kind"], "kinds": []},
        )  # fmt: skip
        entry["failed"] += 1
        for tag in s["failures"]:
            entry["failures"][tag] = entry["failures"].get(tag, 0) + 1
        if s["kind"] not in entry["kinds"]:
            entry["kinds"].append(s["kind"])
    return list(out.values())
