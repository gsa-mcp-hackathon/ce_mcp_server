"""The order rules on a turn's tool calls."""

from ce_eval.trajectory import ToolUse, Transcript, Turn, check_order

GUIDE = ToolUse("capture_guide", {}, {"instructions": "..."})


def submit(status="captured", rid="req-1", **args) -> ToolUse:
    return ToolUse("submit_request_record", args, {"status": status, "request_id": rid})


def run(rid="req-1", name="run_request") -> ToolUse:
    return ToolUse(
        name, {"request_id": rid}, {"outcome": {"request_id": rid}, "reply": "r"}
    )


def check(*calls, **kw):
    kw.setdefault("captured", set())
    return check_order(Turn(user="u", calls=list(calls)), **kw)


def test_the_plain_order_passes():
    order = check(
        GUIDE,
        ToolUse("describe_survey", {}, {}),
        submit(),
        run(),
        link=("none", None),
        wants_figures=True,
    )
    assert order.ok and order.guide_first is True


def test_processing_needs_a_captured_request():
    order = check(GUIDE, submit("needs_clarification"), run())
    assert "which no submission returned as captured" in order.problems[0]
    assert not check(run("req-9")).ok
    assert check(run("req-0"), captured={"req-0"}).ok  # captured in an earlier turn


def test_links_are_checked_on_the_first_submission():
    assert check(submit(follows_request_id="req-0"), link=("follows", "req-0")).ok
    assert not check(submit(), link=("follows", "req-0")).ok
    assert check(submit(answers_request_id="req-0"), link=("answers", "req-0")).ok
    assert not check(submit(follows_request_id="req-0"), link=("answers", "req-0")).ok
    new = check(submit(follows_request_id="req-0"), link=("none", None))
    assert new.problems == ["a new request sent as a follow-up or an answer"]
    assert check(submit(), link=None).ok


def test_a_correction_names_the_failed_submission():
    failed = submit("check_failed", "req-1")
    assert check(failed, submit(rid="req-2", corrects_request_id="req-1")).ok
    assert not check(failed, submit(rid="req-2")).ok


def test_a_captured_request_is_run_when_figures_are_due():
    assert not check(submit(), wants_figures=True).ok
    assert not check(submit(), run(name="evaluate_request"), wants_figures=True).ok
    assert check(submit(), wants_figures=False).ok


def test_the_guide_may_have_been_read_in_an_earlier_turn():
    assert check(submit()).guide_first is False
    assert check(submit(), earlier_guide=True).guide_first is True


def test_a_turn_finds_its_last_request_and_its_outcome():
    turn = Turn(
        "u",
        [
            submit("check_failed", "req-1"),
            submit(rid="req-2"),
            run("req-2", "evaluate_request"),
            run("req-2"),
            ToolUse("describe_table", {"table": "fmli"}, {}),
        ],
    )
    assert turn.last_request.data["request_id"] == "req-2"
    assert turn.processed("req-2").name == "run_request"
    assert turn.processed("req-1") is None
    assert turn.lookups == 1 and len(turn.submissions) == 2


def test_a_transcript_round_trips():
    transcript = Transcript(
        [Turn("u", [submit()], "an answer", {"tokens": 3}, 1.24)], "m", 0.5
    )
    again = Transcript.load(transcript.dump())
    assert again.turns[0].calls[0].data["status"] == "captured"
    assert (again.model, again.cost_usd, again.turns[0].answer) == (
        "m",
        0.5,
        "an answer",
    )
