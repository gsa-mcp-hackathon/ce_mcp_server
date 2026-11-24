"""The judge: reading a verdict, voting, and the labelled examples."""

import pytest

from ce_eval import judge
from tests.eval.conftest import ROOT

CHECKS = ROOT / "eval" / "judge_checks.yaml"


def test_the_verdict_is_the_last_word_of_the_reply():
    read = judge.TextJudge.verdict
    assert (
        read('"…is still under review in the metadata."\nKEPT', "KEPT", "DROPPED")
        is True
    )
    assert read("none\nDROPPED", "KEPT", "DROPPED") is False
    assert read("KEPT? No, on reflection.\nDROPPED", "KEPT", "DROPPED") is False
    assert read("I cannot tell.", "KEPT", "DROPPED") is None
    assert read("", "GIVEN", "MISSING") is None


def test_a_judge_takes_the_majority_and_a_tie_or_silence_is_no_verdict():
    def scripted(*replies):
        answers = list(replies)
        return judge.TextJudge(
            lambda prompt: answers.pop(0), "scripted", votes=len(replies)
        )

    assert scripted("KEPT", "DROPPED", "KEPT").kept("c", "a") is True
    assert scripted("DROPPED", "x\nDROPPED", "KEPT").kept("c", "a") is False
    assert scripted("KEPT", "DROPPED").kept("c", "a") is None
    assert scripted("", "", "").kept("c", "a") is None
    assert scripted("GIVEN").reason_given("r", "a") is True
    assert scripted("SILENT").disclosed("q", "a", "why") is False


def test_each_prompt_carries_its_text_and_asks_for_evidence_first():
    seen = []
    referee = judge.TextJudge(lambda prompt: seen.append(prompt) or "KEPT", "j")
    referee.kept("THE CAUTION", "THE ANSWER")
    referee.reason_given("THE REASON", "THE ANSWER")
    referee.disclosed("THE QUESTION", "THE ANSWER", "THE WHY")
    assert "THE CAUTION" in seen[0] and "THE REASON" in seen[1]
    assert "THE QUESTION" in seen[2] and "THE WHY" in seen[2]
    assert all("THE ANSWER" in p and "First quote" in p for p in seen)


def test_the_labelled_examples_cover_both_verdicts_of_each_kind():
    checks = judge.load_checks(CHECKS)
    assert len(checks) >= 12 and len({c["id"] for c in checks}) == len(checks)
    for kind in ("caution", "reason", "disclosed"):
        verdicts = {c["expect"] for c in checks if c["kind"] == kind}
        assert verdicts == {True, False}, kind


def test_a_judge_is_checked_against_the_examples():
    checks = judge.load_checks(CHECKS)
    always = judge.TextJudge(lambda p: "KEPT\nGIVEN\nDISCLOSED", "yes-man")
    found = judge.check_judge(always, checks)
    wrong = [row["id"] for row in found if not row["agrees"]]
    assert sorted(wrong) == sorted(c["id"] for c in checks if c["expect"] is False)
    with pytest.raises(ValueError):
        judge.ask(always, {"kind": "other"})
