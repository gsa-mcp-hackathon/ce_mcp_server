"""Write the dataset the Databricks MLflow kit evaluates the deployed app on.

    uv run python eval/databricks/build_dataset.py

The kit (`agent_eval_offline.py`) puts one question to an agent and scores the
final answer against `expected_facts`. This script takes the project's own
cases (`eval/cases/`), runs every gold record on the loaded data exactly as
`ce-eval gold --figures` does, and turns what the server replies into those
facts: the figures, the cautions, the reason of a block, the question to ask,
or the decline.

Only cases that stand alone are kept. A case that continues another (a
follow-up, an answer to a question, a correction), the conversations set, and
the cases marked `offline_only` are left out: the kit is single-turn.

The data must be loaded (`ce-ingest load`), since the facts hold figures. The
output is written by code, never by hand; run this again after a change to a
case, a rule, a sidecar or the data.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from ce_eval import offline
from ce_eval.cases import CASES_DIR, Case, load_cases
from ce_eval.cli import _context

OUT = Path(__file__).with_name("ce_eval_dataset.json")
SKIPPED_SETS = ("conversations",)
# The most figures listed for one answered phrase. The scorer fails a row when
# any fact is missing, so a long breakdown (38 states) is checked on its first
# groups and on how many groups it has.
MAX_GROUPS = 8

# A result line of the reply text: "    Northeast: $88,010 (standard error $1,204)"
_RESULT = re.compile(
    r"^ {4}(?P<label>\S.*?): (?P<value>\S.*?)(?: \(standard error .*\))?$"
)


def standalone(case: Case) -> bool:
    """A case one message can test: it continues nothing and needs a model."""
    return not (case.link or case.offline_only or case.set in SKIPPED_SETS)


def result_lines(block: str) -> list[tuple[str, str]]:
    """The (group, figure) pairs of one answer block of a reply text."""
    out, inside = [], False
    for line in block.splitlines():
        if line.strip() == "Result:":
            inside = True
            continue
        if not inside:
            continue
        m = _RESULT.match(line)
        if m is None:
            break
        out.append((m["label"], m["value"]))
    return out


def answer_blocks(request: dict, outcome: dict) -> list[tuple[str, dict, str]]:
    """Each answered phrase with its outcome node and its block of the reply
    text. `compose_reply` writes one block per answer, in order, then a
    closing block."""
    nodes = {n["id"]: n for n in outcome["nodes"]}
    answers = [a for a in request.get("answers") or [] if a["node"] in nodes]
    blocks = (outcome.get("reply") or "").split("\n\n")
    return [
        (a["asks"], nodes[a["node"]], block)
        for a, block in zip(answers, blocks, strict=False)
    ]


def outcome_facts(request: dict, outcome: dict) -> list[str]:
    """What the answer to a captured request must say."""
    facts = []
    for asks, node, block in answer_blocks(request, outcome):
        messages = node.get("messages") or []
        if node["verdict"] == "block":
            facts.append(f'No figure is given for "{asks}": it cannot be computed.')
            facts += [
                f"The reason given: {m['text']}"
                for m in messages
                if m["level"] == "block"
            ]
            continue
        figures = result_lines(block)
        for label, value in figures[:MAX_GROUPS]:
            where = "" if label == "All" else f", {label}"
            facts.append(f'For "{asks}"{where}: {value}.')
        if len(figures) > MAX_GROUPS:
            facts.append(
                f'For "{asks}", a figure is given for each of {len(figures)} groups.'
            )
        facts += [
            f"The answer says what was changed: {m['text']}"
            for m in messages
            if m["level"] == "substitute"
        ]
        facts += [
            f"The answer keeps this caution: {m['text']}"
            for m in messages
            if m["level"] == "warn"
        ]
    return facts


def decline_facts(case: Case) -> list[str]:
    """What the answer to a request the server cannot take must say."""
    facts = ["The answer gives no figure computed from the survey data."]
    reason = case.gold.get("reason")
    may_ask = "needs_clarification" in (case.expect.get("accept_status") or [])
    if reason and may_ask:
        facts.append(
            f"The answer either explains this or asks the user to clarify: {reason}"
        )
    elif reason:
        facts.append(f"The answer explains: {reason}")
    for alternatives in (case.expect.get("answer") or {}).get("mentions") or []:
        facts.append("The answer mentions: " + " or ".join(alternatives) + ".")
    return facts


def row(run: offline.GoldRun) -> dict | None:
    """One dataset row for a gold run, or None when it gives no facts."""
    case = run.case
    kind = offline.expected_kind(run.status, run.request, run.outcome)
    if kind == "question":
        facts = ["The answer gives no figure yet."]
        facts += [
            f"The answer asks the user: {q}" for q in run.reply.get("questions") or []
        ]
    elif kind == "decline":
        facts = decline_facts(case)
    elif run.outcome is not None and run.request is not None:
        facts = outcome_facts(run.request, run.outcome)
    else:
        facts = []
    if not facts:
        return None
    return {
        "case_id": case.id,
        "set": case.set,
        "kind": kind,
        "inputs": {"request": case.message},
        "expectations": {"expected_facts": facts},
    }


def build(cases_dir: Path = CASES_DIR) -> tuple[dict, list[str]]:
    """The dataset document, and the IDs of standalone cases that gave no row."""
    cases = load_cases(cases_dir)
    ctx, data_dir = _context(True)
    env = offline.make_env(ctx, cases.loaded_years, data_dir)
    runs = offline.check(offline.run_gold(env, cases, "run"), env.store)
    rows, empty = [], []
    for run in runs:
        if not standalone(run.case):
            continue
        made = None if run.problems else row(run)
        if made is None:
            empty.append(run.case.id)
        else:
            rows.append(made)
    return {"versions": offline.versions(env), "rows": rows}, empty


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    doc, empty = build(args.cases)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    kinds = Counter(r["kind"] for r in doc["rows"])
    print(f"wrote {args.out}: {len(doc['rows'])} rows, {dict(sorted(kinds.items()))}")
    for case_id in empty:
        print(f"no row for {case_id}: its gold record failed or gave no facts")
    return 1 if empty else 0


if __name__ == "__main__":
    sys.exit(main())
