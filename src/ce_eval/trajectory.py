"""What a model did in a conversation, and whether it used the tools in order.

A transcript is the user's turns, each with the tool calls the model made (the
arguments it sent and what came back) and its final answer. The order rules
are the ones the tool descriptions state:

1. A request is processed (`run_request`, `evaluate_request`) only after a
   submission of it came back captured.
2. An answer to a question is submitted with `answers_request_id` naming the
   request that asked.
3. A follow-up is submitted with `follows_request_id` naming the earlier
   captured request; a new request in the same conversation names none.
4. A submission that failed the check is followed, if at all, by one that
   names it in `corrects_request_id`.
5. A captured request whose answer should hold figures is run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SUBMIT = "submit_request_record"
CAPTURE = "capture_request"  # the server-side path: one call captures
GUIDE = "capture_guide"
PROCESS = ("run_request", "evaluate_request")
LOOKUPS = (
    "describe_survey",
    "list_datasets",
    "describe_table",
    "search_variables",
    "describe_variable",
    "list_geographies",
)


@dataclass
class ToolUse:
    name: str
    arguments: dict[str, Any]
    result: Any  # the parsed result: a dict for every tool of this server

    @property
    def data(self) -> dict:
        return self.result if isinstance(self.result, dict) else {}


@dataclass
class Turn:
    user: str
    calls: list[ToolUse] = field(default_factory=list)
    answer: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    error: str | None = None  # the driver failed: no answer to score

    def uses(self, *names: str) -> list[ToolUse]:
        return [c for c in self.calls if c.name in names]

    @property
    def submissions(self) -> list[ToolUse]:
        return self.uses(SUBMIT, CAPTURE)

    @property
    def last_request(self) -> ToolUse | None:
        """The last submission that stored a record."""
        stored = [c for c in self.submissions if c.data.get("request_id")]
        return stored[-1] if stored else None

    def processed(self, request_id: str | None) -> ToolUse | None:
        """The last run (or, failing one, evaluation) of a request that gave an
        outcome."""
        for names in (("run_request",), ("evaluate_request",)):
            found = [
                c
                for c in self.uses(*names)
                if c.data.get("outcome")
                and c.data["outcome"].get("request_id") == request_id
            ]
            if found:
                return found[-1]
        return None

    @property
    def lookups(self) -> int:
        return len(self.uses(*LOOKUPS))


@dataclass
class Transcript:
    turns: list[Turn] = field(default_factory=list)
    model: str | None = None
    cost_usd: float | None = None

    @classmethod
    def load(cls, doc: dict) -> Transcript:
        """A transcript from what `dump` wrote."""
        turns = [
            Turn(
                user=t["user"],
                calls=[
                    ToolUse(c["name"], c["arguments"], c["result"]) for c in t["calls"]
                ],
                answer=t.get("answer") or "",
                usage=t.get("usage") or {},
                seconds=t.get("seconds") or 0.0,
                error=t.get("error"),
            )
            for t in doc.get("turns") or []
        ]
        return cls(turns=turns, model=doc.get("model"), cost_usd=doc.get("cost_usd"))

    def dump(self) -> dict:
        return {
            "model": self.model,
            "cost_usd": self.cost_usd,
            "turns": [
                {
                    "user": t.user,
                    "calls": [
                        {"name": c.name, "arguments": c.arguments, "result": c.result}
                        for c in t.calls
                    ],
                    "answer": t.answer,
                    "usage": t.usage,
                    "seconds": round(t.seconds, 1),
                    "error": t.error,
                }
                for t in self.turns
            ],
        }


@dataclass
class OrderCheck:
    problems: list[str] = field(default_factory=list)
    guide_first: bool | None = None  # the guide was read before the first record

    @property
    def ok(self) -> bool:
        return not self.problems


def check_order(
    turn: Turn,
    *,
    captured: set[str],
    link: tuple[str, str | None] | None = None,
    wants_figures: bool = False,
    earlier_guide: bool = False,
) -> OrderCheck:
    """Check one turn against the order rules.

    `captured` holds the IDs of requests captured earlier in the conversation;
    `link` is what the turn should link to: ("follows" | "answers", request ID)
    or ("none", None) for a new request, or None when nothing is known.
    """
    out = OrderCheck()
    known = set(captured)
    guided = earlier_guide
    failed: str | None = None
    first_submit = True
    for call in turn.calls:
        if call.name == GUIDE:
            guided = True
        elif call.name in (SUBMIT, CAPTURE):
            args, data = call.arguments, call.data
            if first_submit:
                out.guide_first = guided or call.name == CAPTURE
                first_submit = False
                if link is not None:
                    _check_link(args, link, out)
            if failed and args.get("corrects_request_id") != failed:
                out.problems.append(
                    f"a submission after the failed {failed} does not name it in "
                    "corrects_request_id"
                )
            failed = (
                data.get("request_id") if data.get("status") == "check_failed" else None
            )
            if data.get("status") == "captured" and data.get("request_id"):
                known.add(data["request_id"])
        elif call.name in PROCESS:
            rid = call.arguments.get("request_id")
            if rid not in known:
                out.problems.append(
                    f"{call.name} on {rid}, which no submission returned as captured"
                )
    last = turn.last_request
    if wants_figures and last is not None and last.data.get("status") == "captured":
        if not turn.uses("run_request"):
            out.problems.append("the captured request was never run")
    return out


def _check_link(args: dict, link: tuple[str, str | None], out: OrderCheck) -> None:
    kind, target = link
    follows = args.get("follows_request_id")
    answers = args.get("answers_request_id")
    if kind == "follows" and follows != target:
        out.problems.append(f"a follow-up without follows_request_id = {target}")
    elif kind == "answers" and answers != target:
        out.problems.append(f"an answer without answers_request_id = {target}")
    elif kind == "none" and (follows or answers):
        out.problems.append("a new request sent as a follow-up or an answer")
