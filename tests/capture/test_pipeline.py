"""The pipeline both paths share (`ce_capture.pipeline`): status, storage, links."""

import pytest

from ce_capture.pipeline import LinkError
from tests.capture.conftest import TEXT, node, output, submission


def _record(pipeline, reply):
    return pipeline.store.read("request", reply["request_id"])


def test_a_clean_submission_is_captured_stored_and_resolved(pipeline):
    reply = pipeline.submit(submission())
    assert reply["status"] == "captured"
    assert reply["resolved_id"] == f"res-{reply['request_id'][4:]}-001"
    rec = _record(pipeline, reply)
    assert rec["capture"]["path"] == "client_model" and rec["capture"]["attempts"] == 1
    assert rec["capture"]["agent"] == "ce-capture/1.1"
    assert rec["source"]["turn"] == 1
    assert rec["sidecars"]["tables"] == [
        {"name": "fmli", "id": "ce-interview-fmli", "version": "0.1.0"}
    ]
    assert rec["check"] == {"passed": True, "errors": [], "flags": []}
    assert len(rec["evidence"]) == 4 and rec["submitted"] is None
    assert (
        "resolved" in reply
        and reply["resolved"]["nodes"][0]["weight"] == "None (stated)"
    )
    assert pipeline.submit(submission())["status"] == "captured"
    assert pipeline.store.by_session("s1")[-1]["source"]["turn"] == 2


def test_evidence_is_stored_as_the_exact_span(pipeline):
    out = output()
    out["nodes"][0]["stated"]["weighted"]["evidence"] = "SKIP the weights."
    out["answers"][0]["asks"] = "whats average spending per household by region"
    rec = _record(pipeline, pipeline.submit(submission(out)))
    assert rec["nodes"][0]["stated"]["weighted"]["evidence"] == "Skip the weights"
    assert (
        rec["answers"][0]["asks"] == "what's average spending per household by region"
    )


def test_check_errors_give_check_failed_and_no_resolved_record(pipeline):
    reply = pipeline.submit(
        submission(output(node(measure={"table": "fmli", "column": "totexpq"})))
    )
    assert reply["status"] == "check_failed"
    assert reply["errors"][0]["suggestions"][0] in ("totexppq", "totexpcq")
    assert "resolved_id" not in reply and "corrects_request_id" in reply["next"]


def test_a_schema_failure_is_stored_with_what_was_submitted(pipeline):
    bad = output()
    bad["nodes"][0]["statistic"] = "median"
    reply = pipeline.submit(submission(bad))
    rec = _record(pipeline, reply)
    assert reply["status"] == "check_failed"
    assert rec["check"]["errors"][0]["part"] == "form"
    assert rec["submitted"]["nodes"][0]["statistic"] == "median"
    assert rec["nodes"] == []


def test_statuses_in_order(pipeline):
    not_stat = {
        "nodes": [],
        "reason_code": "not_statistical",
        "reason": "A question about BLS.",
    }
    reply = pipeline.submit(submission(not_stat, text="Who runs the survey?"))
    assert (
        reply["status"] == "not_captured" and reply["reason_code"] == "not_statistical"
    )

    unresolved = output(unresolved=[{"phrase": "by region", "reason": "No column."}])
    reply = pipeline.submit(submission(unresolved))
    assert reply["status"] == "needs_clarification"
    assert reply["questions"] == [
        "I could not match 'by region': No column. Can you say it another way?"
    ]

    ambiguous = output(
        ambiguities=[
            {
                "phrase": "per household",
                "options": ["consumer unit", "dwelling"],
                "chosen": "consumer unit",
                "ask_user": True,
            }
        ]
    )
    reply = pipeline.submit(submission(ambiguous))
    assert reply["questions"] == [
        "By 'per household', do you mean consumer unit or dwelling?"
    ]

    quiet = output(
        ambiguities=[
            {
                "phrase": "per household",
                "options": ["consumer unit", "dwelling"],
                "chosen": "consumer unit",
            }
        ]
    )
    assert pipeline.submit(submission(quiet))["status"] == "captured"

    # errors and questions together: errors come first, the questions are kept
    both = output(
        node(measure={"table": "fmli", "column": "nope"}),
        unresolved=[{"phrase": "by region", "reason": "x"}],
    )
    reply = pipeline.submit(submission(both))
    assert reply["status"] == "check_failed" and reply["questions"]


def test_corrections_and_the_refused_third(pipeline):
    bad = output(node(measure={"table": "fmli", "column": "nope"}))
    first = pipeline.submit(submission(bad))
    second = pipeline.submit(submission(bad, corrects_request_id=first["request_id"]))
    third = pipeline.submit(submission(bad, corrects_request_id=second["request_id"]))
    assert [r["status"] for r in (first, second, third)] == ["check_failed"] * 3
    assert _record(pipeline, third)["capture"]["attempts"] == 3
    with pytest.raises(LinkError, match="corrected 2 times"):
        pipeline.submit(submission(output(), corrects_request_id=third["request_id"]))
    good = pipeline.submit(
        submission(output(), corrects_request_id=first["request_id"])
    )
    assert good["status"] == "captured"
    with pytest.raises(LinkError, match="only a check_failed record"):
        pipeline.submit(submission(output(), corrects_request_id=good["request_id"]))
    with pytest.raises(LinkError, match="same user_text"):
        pipeline.submit(
            submission(
                bad, text="Something else.", corrects_request_id=first["request_id"]
            )
        )


def test_links_must_be_in_the_session_and_have_the_right_status(pipeline):
    captured = pipeline.submit(submission())
    with pytest.raises(LinkError, match="another session"):
        pipeline.submit(
            submission(session_id="s2", follows_request_id=captured["request_id"])
        )
    with pytest.raises(LinkError, match="no request record"):
        pipeline.submit(submission(follows_request_id="req-missing"))
    with pytest.raises(LinkError, match="only a request that needs clarification"):
        pipeline.submit(submission(answers_request_id=captured["request_id"]))
    failed = pipeline.submit(
        submission(output(node(measure={"table": "fmli", "column": "x"})))
    )
    with pytest.raises(LinkError, match="only a captured request is followed"):
        pipeline.submit(submission(follows_request_id=failed["request_id"]))


def test_a_follow_up_patch(pipeline):
    first = pipeline.submit(submission())
    patch = {
        "patch": [
            {
                "op": "set_group_by",
                "node": "*",
                "group_by": [{"table": "fmli", "column": "STATE"}],
            },
            {
                "op": "set_stated",
                "node": "n1",
                "field": "time_basis",
                "value": "quarter",
                "evidence": "per quarter",
            },
        ],
        "answers": [{"asks": "Now by state, per quarter", "node": "n1"}],
    }
    reply = pipeline.submit(
        submission(
            patch,
            text="Now by state, per quarter.",
            follows_request_id=first["request_id"],
        )
    )
    assert reply["status"] == "captured", reply
    rec = _record(pipeline, reply)
    assert rec["nodes"][0]["group_by"] == [{"table": "fmli", "column": "state"}]
    assert rec["carried_over"] == {
        "from_request_id": first["request_id"],
        "unchanged": [],
        "changed": ["n1"],
        "added": [],
        "removed": [],
    }
    assert rec["source"]["earlier_text"] == TEXT
    assert len(rec["answers"]) == 2  # the earlier answer, and the new one
    assert {s["source"] for s in rec["evidence"]} == {"user_text", "earlier_text"}
    resolved = pipeline.store.read("resolved", reply["resolved_id"])
    assert resolved["nodes"][0]["time_basis"] == {
        "value": "quarter",
        "source": "stated",
    }


def test_a_follow_up_to_a_follow_up_may_quote_the_first_message(pipeline):
    """The words of the whole chain are searched for evidence: "as released",
    said once, holds for every follow-up after it."""
    first = pipeline.submit(submission())

    def follow(text, patch, earlier):
        return pipeline.submit(
            submission({"patch": patch, "answers": []}, text=text,
                       follows_request_id=earlier["request_id"])
        )  # fmt: skip

    by_state = [{"op": "set_group_by", "node": "*",
                 "group_by": [{"table": "fmli", "column": "state"}]}]  # fmt: skip
    second = follow("Now by state.", by_state, first)
    assert second["status"] == "captured", second
    renters = [{"op": "add_filter", "node": "*",
                "filter": {"table": "fmli", "column": "cutenure", "comparison": "eq",
                           "value": "4"}}]  # fmt: skip
    third = follow("Only renters.", renters, second)
    assert third["status"] == "captured", third
    rec = _record(pipeline, third)
    assert rec["source"]["earlier_text"] == TEXT + "\nNow by state."
    assert rec["nodes"][0]["stated"]["gap_2025"]["value"] == "as_released"
    spans = {s["where"]: s for s in rec["evidence"]}
    gap = spans["n1.stated.gap_2025"]
    assert gap["source"] == "earlier_text"
    assert rec["source"]["earlier_text"][gap["start"] : gap["end"]] == "as released"


def test_patch_errors(pipeline):
    first = pipeline.submit(submission())
    reply = pipeline.submit(
        submission(
            {"patch": [{"op": "remove_node", "node": "n9"}]},
            text="Drop it.",
            follows_request_id=first["request_id"],
        )
    )
    assert reply["status"] == "check_failed"
    assert "the nodes are: n1" in reply["errors"][0]["message"]
    reply = pipeline.submit(submission({"patch": []}, text="Hm."))
    assert "needs follows_request_id" in reply["errors"][0]["message"]
    reply = pipeline.submit(
        submission(output() | {"patch": []}, follows_request_id=first["request_id"])
    )
    assert "not both" in reply["errors"][0]["message"]


def test_an_answer_to_a_clarification(pipeline):
    asked = output()
    del asked["nodes"][0]["stated"]["gap_2025"]
    text = (
        "For 2025, what's average spending per household by region? Skip the weights."
    )
    q = pipeline.submit(submission(asked, text=text))
    assert q["status"] == "needs_clarification"
    answer = output()
    answer["nodes"][0]["stated"]["gap_2025"] = {
        "value": "adjusted",
        "evidence": "Adjusted",
    }
    reply = pipeline.submit(
        submission(answer, text="Adjusted, please.", answers_request_id=q["request_id"])
    )
    assert reply["status"] == "captured"
    assert any(
        f.startswith("adjusted_calendar_pair") for f in reply["resolved"]["flags"]
    )
    assert _record(pipeline, reply)["source"]["earlier_text"] == text
