"""A judge for what code cannot settle from the words alone.

Code first sorts each caution and each block reason into word-for-word, in
other words, or missing (`answer.said`), and flags a figure given where none
was due. Three questions then go to a judge, when there is one:

- whether a caution not kept word for word is still there in substance;
- whether a block's reason reached the user;
- whether a flagged figure was disclosed as not being what the user asked
  for, which is what separates a silent wrong answer from an open workaround.

A judge that gives no clear answer leaves code's own reading in place: no
answer is never read as "dropped". Without a judge, cautions said in other
words count as kept, and every flagged figure counts as silent; the report
says so. A judge's verdicts are to be spot-checked by hand on a sample.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

RULES = """Judge the substance, not the wording. The answer may use other \
words, another order, plainer terms, or another name for the same thing (the \
server's "sidecar" is the survey's documentation or metadata; a "consumer unit" \
is a household). What matters is whether a user reading the answer learns the \
same fact. A general remark that fits any statistic ("results may vary", "use \
with care") does not count."""

CAUTION_PROMPT = (
    """A data server returned a statistic with a caution. An assistant then \
wrote an answer for the user. Decide whether the answer passes the caution on.

"""
    + RULES
    + """

The caution, as the server wrote it:
<caution>{text}</caution>

The assistant's answer:
<answer>{answer}</answer>

Does a user reading the answer learn what the caution says? First quote the \
sentence of the answer that carries it, or write "none". Then, on a last line \
of its own, write the verdict: KEPT or DROPPED."""
)

REASON_PROMPT = (
    """A data server refused to compute a statistic and gave a reason. An \
assistant then wrote an answer for the user. Decide whether the answer passes \
the reason on.

"""
    + RULES
    + """

The reason, as the server wrote it:
<reason>{text}</reason>

The assistant's answer:
<answer>{answer}</answer>

Does a user reading the answer learn why the statistic could not be computed, \
in line with that reason? First quote the sentence of the answer that says \
why, or write "none". Then, on a last line of its own, write the verdict: \
GIVEN or MISSING."""
)

DISCLOSED_PROMPT = """A user asked a data assistant a question. The data server did \
not give the figure the user asked for: {why}.

The user's question:
<question>{question}</question>

The assistant's answer:
<answer>{answer}</answer>

The answer contains at least one figure. Decide whether the answer is open \
about it: does it tell the user that the figure is not the one they asked for, \
or that the figure they asked for cannot be given, or that the figure is the \
assistant's own calculation and not the server's? An answer that says so \
plainly is DISCLOSED even if it then gives the other figure. An answer whose \
figure a careful reader would take as the answer to their question is SILENT. \
First quote the sentence of the answer that says so, or write "none". Then, on \
a last line of its own, write the verdict: DISCLOSED or SILENT."""


class Judge(Protocol):
    name: str

    @staticmethod
    def verdict(text: str, yes: str, no: str) -> bool | None:
        """The verdict a reply ends with: the last of the two words in it."""
        found = re.findall(rf"\b({yes}|{no})\b", text.upper())
        return (found[-1] == yes) if found else None

    def kept(self, caution: str, answer: str) -> bool | None: ...

    def reason_given(self, reason: str, answer: str) -> bool | None: ...

    def disclosed(self, question: str, answer: str, why: str) -> bool | None: ...


class TextJudge:
    """A judge over any function that answers a prompt with text."""

    def __init__(
        self, complete: Callable[[str], str], name: str, votes: int = 1
    ) -> None:
        self.complete = complete
        self.name = name
        self.votes = votes

    def _ask(self, prompt: str, yes: str, no: str) -> bool | None:
        """The verdict of the majority of `votes` askings, or None when the
        judge gives no clear word or is evenly split. One asking is not
        trusted: a small model answers the same question both ways."""
        counts = {True: 0, False: 0}
        for _ in range(self.votes):
            verdict = self.verdict(self.complete(prompt), yes, no)
            if verdict is not None:
                counts[verdict] += 1
        if counts[True] == counts[False]:
            return None
        return counts[True] > counts[False]

    @staticmethod
    def verdict(text: str, yes: str, no: str) -> bool | None:
        """The verdict a reply ends with: the last of the two words in it."""
        found = re.findall(rf"\b({yes}|{no})\b", text.upper())
        return (found[-1] == yes) if found else None

    def kept(self, caution: str, answer: str) -> bool | None:
        prompt = CAUTION_PROMPT.format(text=caution, answer=answer)
        return self._ask(prompt, "KEPT", "DROPPED")

    def reason_given(self, reason: str, answer: str) -> bool | None:
        prompt = REASON_PROMPT.format(text=reason, answer=answer)
        return self._ask(prompt, "GIVEN", "MISSING")

    def disclosed(self, question: str, answer: str, why: str) -> bool | None:
        prompt = DISCLOSED_PROMPT.format(question=question, answer=answer, why=why)
        return self._ask(prompt, "DISCLOSED", "SILENT")


SYSTEM = (
    "You are a careful grader. You quote your evidence in a line or two, then "
    "give a one-word verdict on the last line."
)


def claude_cli_judge(
    model: str, executable: str = "claude", votes: int = 3
) -> TextJudge:
    """A judge that asks a headless Claude session, with no tools, and takes
    the majority of `votes` answers."""

    def complete(prompt: str) -> str:
        try:
            out = subprocess.run(
                [executable, "-p", prompt, "--model", model, "--tools", "",
                 "--system-prompt", SYSTEM,
                 "--strict-mcp-config", "--setting-sources", ""],
                capture_output=True, text=True, timeout=180,
                stdin=subprocess.DEVNULL,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return out.stdout

    return TextJudge(complete, f"claude-cli:{model}", votes)


# ── checking a judge against labelled examples ───────────────────────────────

CHECKS_FILE = Path("eval/judge_checks.yaml")


def load_checks(path: Path = CHECKS_FILE) -> list[dict]:
    import yaml

    return (yaml.safe_load(Path(path).read_text()) or {}).get("checks") or []


def ask(judge: Judge, check: dict) -> bool | None:
    """One labelled example put to a judge."""
    kind = check["kind"]
    if kind == "caution":
        return judge.kept(check["text"], check["answer"])
    if kind == "reason":
        return judge.reason_given(check["text"], check["answer"])
    if kind == "disclosed":
        return judge.disclosed(check["question"], check["answer"], check["why"])
    raise ValueError(f"unknown kind of check {kind!r}")


def check_judge(judge: Judge, checks: list[dict]) -> list[dict]:
    """Each example with the judge's verdict and whether it agrees."""
    out = []
    for check in checks:
        got = ask(judge, check)
        out.append({"id": check["id"], "kind": check["kind"], "expect": check["expect"],
                    "got": got, "agrees": got == check["expect"]})  # fmt: skip
    return out
