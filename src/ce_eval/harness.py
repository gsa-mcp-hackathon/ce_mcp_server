"""Running the cases on a model and scoring what it did (Plan 6, runs 2 and 3).

Each conversation is put to a driver in a fresh record store. Every turn is
then scored against its case at three levels: the record the model stored, the
outcome that record leads to, and the answer the user received, with the tool
order beside them. The gold side is produced the same way as in the offline
check, in the same environment, so both sides are judged by the same code.
"""

from __future__ import annotations

import json
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ce_capture.context import CaptureContext
from ce_capture.gold import compare
from ce_capture.rules import load_phrases
from ce_capture.text import has_phrase, words
from ce_eval import answer as answers
from ce_eval.cases import Case, CaseSet
from ce_eval.drivers import Driver
from ce_eval.judge import Judge
from ce_eval.offline import Env, GoldRun, expected_kind, make_env, run_gold
from ce_eval.signature import answered, equivalence
from ce_eval.trajectory import Transcript, Turn, check_order

NO_FIGURE_KINDS = ("block", "question", "decline")
# Where a failure of each kind is usually fixed; a person confirms it in triage.
LIKELY_FIX = {
    "silent_wrong": "tool description (what to do with a block or a question)",
    "worked_around": "tool description (what to do with a block or a question)",
    "missed_answer": "capture instructions, or the lookup answer that hid the column",
    "status": "capture instructions",
    "outcome": "capture instructions, or the lookup answer that hid the column",
    "fabricated_stated": "capture instructions (only what the user's words state)",
    "numbers": "tool description (show the reply as written)",
    "block_honesty": "tool description (a block gets its reason, no figure)",
    "question": "capture reply (ask the questions as written)",
    "cautions": "tool description (show the reply as written)",
    "tool_order": "tool description (the order of the calls)",
    "mentions": "the model",
    "error": "the driver or the model",
}


@dataclass
class TurnScore:
    case: str
    set: str
    nuance: list[str]
    run: int
    model: str
    gold_kind: str
    kind: str  # what the model's own record and outcome lead to
    gold_status: str | None
    status: str | None
    status_ok: bool
    outcome: float  # the share of answered statistics that match the gold's
    fields: dict[str, float] = field(default_factory=dict)
    stated_extra: int = 0
    asked_ok: bool | None = None  # an ambiguity or unresolved phrase was raised
    first_pass: bool | None = None
    attempts: int = 0
    lookups: int = 0
    faithful_numbers: bool = True
    unsupported: list[str] = field(default_factory=list)
    has_figure: bool = False
    block_honest: bool | None = None
    question_asked: bool | None = None
    cautions: dict[str, int] = field(default_factory=dict)
    cautions_optional: bool = False
    mentions_ok: bool | None = None
    order_ok: bool = True
    order_problems: list[str] = field(default_factory=list)
    guide_first: bool | None = None
    silent_wrong: bool = False
    worked_around: bool = False  # a figure that is not what was asked, said openly
    missed: bool = False
    tokens: int = 0
    seconds: float = 0.0
    error: str | None = None
    failures: list[str] = field(default_factory=list)
    signature: str = ""  # status and what was computed, for consistency
    detail: dict = field(default_factory=dict)


# The statistics a stated choice has any effect on; elsewhere it changes nothing.
APPLIES = {
    "count_unit": ("count", "share"),
    "time_basis": ("mean", "total"),
    "missing_codes": ("mean", "total"),
    "imputation": ("mean", "total"),
}


def fabricated_stated(request: dict | None, gold: dict | None) -> list[str]:
    """Stated choices the user's words do not support: a choice the gold record
    does not state, on a node it has an effect on, when the user's messages
    hold none of the phrases that state that choice (`phrases.yaml`). Weights
    stated for "average spending by region" is one; a count unit stated from
    "how many households" is not, whatever fragment the model quoted."""
    phrases = load_phrases().choices
    in_gold = {
        name
        for node in (gold or {}).get("nodes") or []
        for name, choice in (node.get("stated") or {}).items()
        if choice
    }
    source = (request or {}).get("source") or {}
    said = words(f"{source.get('earlier_text') or ''} {source.get('user_text') or ''}")
    out = []
    for node in (request or {}).get("nodes") or []:
        for name, choice in (node.get("stated") or {}).items():
            if not choice or name in in_gold:
                continue
            if name in APPLIES and node.get("statistic") not in APPLIES[name]:
                continue
            if any(p.field == name and has_phrase(said, p.phrase) for p in phrases):
                continue
            out.append(f"{node.get('id')}.{name}")
    return out


def _view(record: dict | None, status: str | None) -> dict:
    return {"status": status, "nodes": (record or {}).get("nodes") or []}


def _context_texts(transcript: Transcript, upto: int) -> list[str]:
    """What the model may quote without it being its own figure: the user's
    messages, every lookup and capture result, and the replies of earlier turns."""
    texts = []
    for i, turn in enumerate(transcript.turns[: upto + 1]):
        texts.append(turn.user)
        for call in turn.calls:
            processing = call.name in ("run_request", "evaluate_request")
            if processing and i == upto:
                continue
            texts.append(json.dumps(call.result, default=str))
            texts.append(json.dumps(call.arguments, default=str))
    return texts


def score_turn(
    case: Case,
    turn: Turn,
    gold: GoldRun,
    env: Env,
    transcript: Transcript,
    index: int,
    *,
    link: tuple[str, str | None] | None,
    captured: set[str],
    earlier_guide: bool,
    run: int = 0,
    judge: Judge | None = None,
) -> TurnScore:
    """Score one turn of a conversation against its case."""
    sub = turn.last_request
    # a model that answers without storing a record has, in effect, not captured
    status = sub.data.get("status") if sub else (None if turn.error else "not_captured")
    request = env.store.read("request", sub.data["request_id"]) if sub else None
    resolved = (
        env.store.read("resolved", sub.data["resolved_id"])
        if sub and sub.data.get("resolved_id")
        else None
    )
    processed = turn.processed(sub.data.get("request_id")) if sub else None
    outcome = processed.data["outcome"] if processed else None
    kind = expected_kind(status, request, outcome)
    computed = answered(request, resolved) if request and status == "captured" else []

    # the gold record, or the alternative the model's record is closest to
    accept = case.expect.get("accept_status") or [gold.status]
    status_ok = status in accept
    same = 0.0
    for candidate in [gold, *gold.alternatives]:
        if candidate.status != "captured":
            score_ = 1.0 if status_ok else 0.0
        elif status == "captured":
            wanted = answered(candidate.request or {}, candidate.resolved)
            score_ = equivalence(computed, wanted)
        else:
            score_ = 0.0
        if score_ > same or candidate is gold:
            same, best = score_, candidate
    gold_kind = expected_kind(best.status, best.request, best.outcome)
    fields = compare(_view(request, status), _view(best.request, best.status))
    invented = fabricated_stated(request, best.request)
    if status_ok and gold_kind == "block" and kind in ("block", "decline"):
        # blocked or declined where the gold blocks: the user gets no figure for
        # it either way, whatever record led there
        same, invented = 1.0, []

    expect_answer = case.expect.get("answer") or {}
    replies = [
        c.data["reply"]
        for c in turn.uses("run_request", "evaluate_request")
        if c.data.get("reply")
    ]
    check = answers.check_answer(
        turn.answer,
        outcomes=[(request, outcome)] if outcome else [],
        questions=list(sub.data.get("questions") or [])
        if sub and status == "needs_clarification"
        else [],
        reply_texts=replies,
        context_texts=_context_texts(transcript, index),
        mentions=expect_answer.get("mentions"),
    )
    judged: dict[str, bool | None] = {}
    if judge is not None:
        for text, state in check.cautions.items():
            if state != "verbatim":
                judged[text] = judge.kept(text, turn.answer)
        answers.apply_judgments(check.cautions, judged)
        reasons = {
            text: judge.reason_given(text, turn.answer)
            for text, state in check.reasons.items()
            if state != "verbatim"
        }
        answers.apply_judgments(check.reasons, reasons)

    wants_figures = gold_kind in ("figures", "mixed")
    order = check_order(
        turn,
        captured=captured,
        link=link,
        wants_figures=wants_figures and status == "captured",
        earlier_guide=earlier_guide,
    )
    has_figure = check.has_result_figure
    own = bool(check.figures.unsupported)  # figures the server did not give
    vouched = has_figure and len(check.figures.results) > len(check.figures.unsupported)
    # A blocked statistic gets its reason and no figure of the model's own; a
    # figure the server computed for another part of the request may be shown.
    block_honest = None
    if kind in ("block", "mixed"):
        block_honest = check.reason_given and not own
    question_asked = None
    if status == "needs_clarification":
        question_asked = check.question_asked and not has_figure
    asks = bool(gold.request and (
        gold.request.get("unresolved")
        or any(a.get("ask_user") for a in gold.request.get("ambiguities") or [])
    ))  # fmt: skip

    # A wrong answer: a figure where the model computed something other than
    # what was asked, or a figure of its own where none was due or none was run.
    equivalent = same >= 1.0 and status_ok
    why = None
    if has_figure and not equivalent:
        why = "the assistant's request differs from what the user asked for"
    elif own and gold_kind in NO_FIGURE_KINDS:
        why = "the right outcome was a refusal or a question, with no figure"
    elif own and not vouched:
        why = "the server returned no figure for it"
    disclosed = None
    if why and judge is not None:
        disclosed = judge.disclosed(case.message, turn.answer, why)
    silent_wrong = bool(why) and disclosed is not True
    worked_around = bool(why) and disclosed is True
    states = list(check.cautions.values())
    cautions = {
        "total": len(states),
        "verbatim": states.count("verbatim"),
        "paraphrase": states.count("paraphrase"),
        "missing": states.count("missing"),
        "judged": len(judged),
    }
    optional = expect_answer.get("cautions") == "optional"
    score = TurnScore(
        case=case.id,
        set=case.set,
        nuance=list(case.nuance),
        run=run,
        model=transcript.model or "",
        gold_kind=gold_kind,
        kind=kind,
        gold_status=gold.status,
        status=status,
        status_ok=status_ok,
        outcome=same,
        fields=fields,
        stated_extra=len(invented),
        asked_ok=(status == "needs_clarification" or status_ok) if asks else None,
        first_pass=turn.submissions[0].data.get("status") != "check_failed"
        if turn.submissions
        else None,
        attempts=len(turn.submissions),
        lookups=turn.lookups,
        faithful_numbers=check.faithful_numbers,
        unsupported=check.figures.unsupported,
        has_figure=has_figure,
        block_honest=block_honest,
        question_asked=question_asked,
        cautions=cautions,
        cautions_optional=optional,
        mentions_ok=not check.mentions_missing
        if expect_answer.get("mentions")
        else None,
        order_ok=order.ok,
        order_problems=order.problems,
        guide_first=order.guide_first,
        silent_wrong=silent_wrong,
        worked_around=worked_around,
        missed=wants_figures and not has_figure,
        tokens=sum(v for k, v in turn.usage.items() if "token" in k),
        seconds=turn.seconds,
        error=turn.error,
        signature=f"{status}:{','.join(computed)}",
        detail={
            "answer": turn.answer,
            "check": answers.dump(check),
            "invented_stated": invented,
            "flagged": why,
            "disclosed": disclosed,
            "matched_alternative": best is not gold,
        },
    )
    score.failures = failures(score)
    return score


def failures(s: TurnScore) -> list[str]:
    """What went wrong in a turn, as tags the report counts."""
    out = []
    if s.error:
        out.append("error")
    if s.silent_wrong:
        out.append("silent_wrong")
    if s.worked_around:
        out.append("worked_around")
    if s.missed and not s.error:
        out.append("missed_answer")
    if not s.status_ok:
        out.append("status")
    if s.outcome < 1.0 and s.status_ok:
        out.append("outcome")
    if s.stated_extra:
        out.append("fabricated_stated")
    if not s.faithful_numbers:
        out.append("numbers")
    if s.block_honest is False:
        out.append("block_honesty")
    if s.question_asked is False:
        out.append("question")
    if s.cautions.get("missing") and not s.cautions_optional:
        out.append("cautions")
    if not s.order_ok:
        out.append("tool_order")
    if s.mentions_ok is False:
        out.append("mentions")
    return out


def score_conversation(
    conv: list[Case],
    transcript: Transcript,
    env: Env,
    gold: dict[str, GoldRun],
    run: int = 0,
    judge: Judge | None = None,
) -> list[TurnScore]:
    """Score every turn of one conversation."""
    scores = []
    stored: dict[str, str] = {}  # case id -> the request the model stored
    statuses: dict[str, str | None] = {}
    captured: set[str] = set()
    guided = False
    for index, case in enumerate(conv):
        if index >= len(transcript.turns):
            turn = Turn(user=case.message, error="the conversation stopped earlier")
        else:
            turn = transcript.turns[index]
        # A link is due only when the earlier turn left what the case expects:
        # a captured request to follow, or a question to answer. When the model
        # went another way earlier (it captured where a question was due), the
        # link has nothing to name, and is not checked.
        link: tuple[str, str | None] | None = ("none", None)
        if case.follows:
            ok = statuses.get(case.follows) == "captured"
            link = ("follows", stored.get(case.follows)) if ok else None
        elif case.answers:
            ok = statuses.get(case.answers) == "needs_clarification"
            link = ("answers", stored.get(case.answers)) if ok else None
        scores.append(
            score_turn(
                case,
                turn,
                gold[case.id],
                env,
                transcript,
                index,
                link=link,
                captured=captured,
                earlier_guide=guided,
                run=run,
                judge=judge,
            )  # fmt: skip
        )
        guided = guided or bool(turn.uses("capture_guide"))
        last = turn.last_request
        if last is not None:
            stored[case.id] = last.data["request_id"]
            statuses[case.id] = last.data.get("status")
        for sub in turn.submissions:
            if sub.data.get("status") == "captured":
                captured.add(sub.data["request_id"])
    return scores


def _conversations(cases: CaseSet) -> list[list[Case]]:
    """The conversations a model is given: cases marked offline are left out."""
    kept = [[c for c in conv if not c.offline_only] for conv in cases.conversations()]
    return [conv for conv in kept if conv]


def _gold(ctx: CaptureContext, cases: CaseSet, data_dir: Path | None):
    mode = "run" if data_dir else "evaluate"
    gold_env = make_env(ctx, cases.loaded_years, data_dir)
    return mode, {r.case.id: r for r in run_gold(gold_env, cases, mode)}


def run_model(
    ctx: CaptureContext,
    cases: CaseSet,
    driver: Driver,
    *,
    data_dir: Path | None = None,
    repeat: int = 1,
    judge: Judge | None = None,
    records: Path | None = None,
    workers: int = 1,
    partial: Path | None = None,
    progress=None,
) -> dict:
    """Put every conversation to a driver `repeat` times and score each turn.

    Returns the results document: the scores, and under `transcripts` what the
    model did. Each conversation's records are kept under `records`, so the
    results can be scored again later (`rescore`). `workers` above 1 runs
    conversations side by side; only a driver that starts its own server
    (`parallel`) may. With `partial`, each finished conversation is appended
    to that file, and a run started again with the same file and records
    folder takes up where it stopped, so a long run that is interrupted loses
    nothing.
    """
    mode, gold = _gold(ctx, cases, data_dir)
    records = Path(records) if records else Path(tempfile.mkdtemp(prefix="ce-eval-"))
    jobs = [(i, conv) for i in range(repeat) for conv in _conversations(cases)]

    finished: dict[tuple[int, str], dict] = {}
    if partial and Path(partial).exists():
        for line in Path(partial).read_text().splitlines():
            entry = json.loads(line)
            finished[(entry["run"], entry["conversation"])] = entry
    lock = threading.Lock()

    def one(job: tuple[int, list[Case]]):
        i, conv = job
        folder = records / f"run-{i}" / conv[0].id
        env = make_env(ctx, cases.loaded_years, data_dir, records_dir=folder)
        earlier = finished.get((i, conv[0].id))
        if earlier is not None:
            transcript = Transcript.load(earlier)
        else:
            transcript = driver.converse([c.message for c in conv], env)
            if partial:
                entry = {"run": i, "conversation": conv[0].id, **transcript.dump()}
                with lock, Path(partial).open("a") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
        scored = score_conversation(conv, transcript, env, gold, i, judge)
        return i, conv, transcript, scored

    if workers > 1 and getattr(driver, "parallel", False):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(workers) as pool:
            done = pool.map(one, jobs)
            done = [(_report(r, progress), r)[1] for r in done]
    else:
        done = [(_report(r, progress), r)[1] for r in map(one, jobs)]
    scores = [s for _, _, _, turn_scores in done for s in turn_scores]
    return {
        "model": driver.name,
        "judge": judge.name if judge else None,
        "repeat": repeat,
        "mode": mode,
        "records": str(records),
        "cases": len({s.case for s in scores}),
        "cost_usd": sum(t.cost_usd or 0.0 for _, _, t, _ in done),
        "scores": [asdict(s) for s in scores],
        "transcripts": [
            {"run": i, "conversation": conv[0].id, **t.dump()} for i, conv, t, _ in done
        ],
    }


def _report(result, progress) -> None:
    if progress:
        for score in result[3]:
            progress(score)


def rescore(
    results: dict,
    ctx: CaptureContext,
    cases: CaseSet,
    *,
    data_dir: Path | None = None,
    judge: Judge | None = None,
    workers: int = 1,
) -> dict:
    """Score a results document again from its transcripts and kept records,
    with no model run: for when the checkers or the cases change."""
    _, gold = _gold(ctx, cases, data_dir)
    by_root = {conv[0].id: conv for conv in _conversations(cases)}
    records = Path(results["records"])

    def one(entry: dict) -> list[TurnScore]:
        conv = by_root.get(entry["conversation"])
        if conv is None:
            return []
        folder = records / f"run-{entry['run']}" / entry["conversation"]
        env = make_env(ctx, cases.loaded_years, data_dir, records_dir=folder)
        transcript = Transcript.load(entry)
        return score_conversation(conv, transcript, env, gold, entry["run"], judge)

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max(workers, 1)) as pool:
        scores = [s for found in pool.map(one, results["transcripts"]) for s in found]
    out = {k: v for k, v in results.items() if k != "summary"}
    out["scores"] = [asdict(s) for s in scores]
    out["cases"] = len({s.case for s in scores})
    out["judge"] = judge.name if judge else results.get("judge")
    return out
