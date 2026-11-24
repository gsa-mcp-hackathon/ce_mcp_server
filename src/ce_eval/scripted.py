"""Scripted models: a model that does everything right, and the same model
with one fault at a time.

`Oracle` follows the gold records: it reads the guide, submits the gold record
of the case whose message it is given, runs a captured request and repeats the
server's reply word for word. Running the harness on it must give a perfect
score, which checks the harness. Each fault breaks one thing a real model
gets wrong, and the matching checker must catch it, which checks the
checkers.
"""

from __future__ import annotations

import json
import re

from ce_capture.model import ChatReply, ToolCall
from ce_eval.cases import Case, CaseSet

FAULTS = (
    "drop_cautions",  # the reply without its Caution lines
    "fabricate",  # a figure of the model's own added to the answer
    "answer_on_block",  # a figure given for a blocked statistic
    "answer_instead_of_asking",  # a figure given where a question was due
    "run_unresolved",  # run_request on a request that needs clarification
    "no_link",  # follow-ups and answers sent with no link
    "wrong_measure",  # another column than the one asked for
    "invent_weights",  # a stated choice the user never made
    "round_figures",  # every figure rounded to thousands
)
DECLINE = "This cannot be answered from the CE Interview Survey: {reason}"


class Oracle:
    """A `ModelClient` that plays the gold records of a case set."""

    def __init__(self, cases: CaseSet, faults: tuple[str, ...] = ()) -> None:
        unknown = set(faults) - set(FAULTS)
        if unknown:
            raise ValueError(f"unknown faults: {sorted(unknown)}")
        self.cases = cases
        self.by_message = {c.message: c for c in cases.cases}
        self.faults = set(faults)
        self.name = "oracle" + ("".join(f"+{f}" for f in sorted(faults)))
        self.ids: dict[str, str] = {}  # case id -> the request it stored
        self.guided = False
        self._n = 0

    # ── reading the conversation ─────────────────────────────────────────────

    def _call(self, name: str, arguments: dict) -> ChatReply:
        self._n += 1
        return ChatReply(None, (ToolCall(f"call-{self._n}", name, arguments),))

    @staticmethod
    def _this_turn(messages: list[dict]) -> tuple[str, list[dict]]:
        last = max(i for i, m in enumerate(messages) if m["role"] == "user")
        return messages[last]["content"], messages[last + 1 :]

    @staticmethod
    def _results(after: list[dict]) -> list[tuple[str, dict]]:
        names = {}
        out = []
        for m in after:
            for c in m.get("tool_calls") or []:
                names[c["id"]] = c["function"]["name"]
            if m["role"] == "tool":
                out.append((names.get(m["tool_call_id"], ""), json.loads(m["content"])))
        return out

    # ── the gold record, with a fault if asked ───────────────────────────────

    def _record(self, case: Case) -> dict:
        record = json.loads(json.dumps(case.gold))
        for node in record.get("nodes") or []:
            if node.get("kind") != "estimate":
                continue
            if "wrong_measure" in self.faults and node.get("measure"):
                spending = node["measure"].get("column") in ("totexpcq", "totexppq")
                other = "fincbtxm" if spending else "totexpcq"
                node["measure"] = {"table": "fmli", "column": other}
            if "invent_weights" in self.faults:
                stated = node.setdefault("stated", {})
                if "weighted" not in stated:
                    word = case.message.split()[0]
                    stated["weighted"] = {"value": True, "evidence": word}
        return record

    def _submit(self, case: Case) -> ChatReply:
        args: dict = {
            "user_text": case.message,
            "session_id": f"s-{self.cases.root(case).id}",
            "record": self._record(case),
        }
        if "no_link" not in self.faults:
            for key in ("follows", "answers", "corrects"):
                target = getattr(case, key)
                if target and target in self.ids:
                    args[f"{key}_request_id"] = self.ids[target]
        return self._call("submit_request_record", args)

    # ── the model ────────────────────────────────────────────────────────────

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatReply:
        text, after = self._this_turn(messages)
        case = self.by_message.get(text)
        if case is None:
            return ChatReply("I have no script for this message.")
        results = self._results(after)
        offered = {t["function"]["name"] for t in tools}
        if not results:
            if not self.guided and "capture_guide" in offered:
                self.guided = True
                return self._call("capture_guide", {})
            return self._submit(case)
        name, data = results[-1]
        if name == "capture_guide":
            return self._submit(case)
        if name == "submit_request_record":
            if data.get("request_id"):
                self.ids[case.id] = data["request_id"]
            status = data.get("status")
            if status == "captured":
                return self._call("run_request", {"request_id": data["request_id"]})
            if status == "needs_clarification":
                if "run_unresolved" in self.faults:
                    return self._call("run_request", {"request_id": data["request_id"]})
                if "answer_instead_of_asking" in self.faults:
                    return ChatReply("It was about $61,300 per household.")
                return ChatReply("\n".join(data.get("questions") or []))
            if status == "not_captured":
                reason = case.gold.get("reason") or "it is not a statistics request."
                return ChatReply(DECLINE.format(reason=reason))
            return ChatReply(data.get("error") or "The request could not be captured.")
        if name == "run_request":
            if data.get("error"):
                return ChatReply(data["error"])
            return ChatReply(self._present(data.get("reply", "")))
        return ChatReply("Done.")

    def _present(self, reply: str) -> str:
        if "answer_on_block" in self.faults and "Cannot compute" in reply:
            return "The figure is roughly $48,500 per household."
        text = reply
        if "drop_cautions" in self.faults:
            text = _without_cautions(text)
        if "round_figures" in self.faults:
            text = re.sub(
                r"\$(\d{1,3}),(\d{3})\b",
                lambda m: f"${round(int(m[1] + m[2]), -3):,}",
                text,
            )
        if "fabricate" in self.faults:
            text += "\nFor comparison, the national figure last year was $72,967."
        return text


LABELS = ("Computed", "Will compute", "Cannot compute", "Changed", "Result", "From ",
          "Removed before computing")  # fmt: skip


def _without_cautions(reply: str) -> str:
    """A reply with its Caution lines, and their wrapped continuations, cut."""
    kept, cutting = [], False
    for line in reply.splitlines():
        if line.startswith("  Caution:"):
            cutting = True
            continue
        continuation = (
            line.startswith("  ")
            and not line.startswith("    ")
            and not line.strip().startswith(LABELS)
        )
        if cutting and continuation:
            continue
        cutting = False
        kept.append(line)
    return "\n".join(kept)
