"""The server-side loop (`ce_capture.agent`) with a fake model."""

import json

import pytest

from ce_capture.agent import ServerCapture
from ce_capture.model import ChatReply, FakeModel, ModelError, call
from ce_capture.pipeline import SERVER_CHECK_FAILED
from tests.capture.conftest import TEXT, node, output

BAD = output(node(measure={"table": "fmli", "column": "totexpq"}))
GOOD = output()


def _submit(out, cid="c"):
    return ChatReply(None, (call("submit_record", out, cid),))


def _tool_answers(model, i=-1):
    return [json.loads(m["content"]) for m in model.calls[i][0] if m["role"] == "tool"]


def test_lookups_then_a_record(pipeline):
    model = FakeModel(
        [
            ChatReply(
                None,
                (
                    call("describe_survey", {}),
                    call(
                        "search_variables",
                        {"query": "spending", "usable_as": "measure"},
                    ),
                ),
            ),
            _submit(GOOD),
        ]
    )
    reply = ServerCapture(pipeline, model).capture(TEXT, "s1")
    assert reply["status"] == "captured"
    rec = pipeline.store.read("request", reply["request_id"])
    assert (rec["capture"]["path"], rec["capture"]["model"]) == (
        "server_model",
        "fake-model",
    )
    assert (rec["capture"]["attempts"], rec["capture"]["lookups"]) == (1, 2)
    answers = _tool_answers(model)
    assert answers[0]["weight"]["value"]["column"] == "finlwt21"
    assert "matches" in answers[1]
    system = model.calls[0][0][0]
    assert (
        system["role"] == "system"
        and "Capturing a CE Interview request" in system["content"]
    )


def test_errors_go_back_to_the_model_and_only_the_last_record_is_stored(pipeline):
    model = FakeModel([_submit(BAD, "c1"), _submit(GOOD, "c2")])
    reply = ServerCapture(pipeline, model).capture(TEXT, "s1")
    assert reply["status"] == "captured"
    assert len(pipeline.store.by_session("s1")) == 1
    errors = _tool_answers(model)[0]
    assert (
        errors["passed"] is False and errors["errors"][0]["field"] == "measure.column"
    )
    rec = pipeline.store.read("request", reply["request_id"])
    assert rec["capture"]["attempts"] == 2


def test_after_the_retries_the_record_is_stored_as_check_failed(pipeline):
    model = FakeModel([_submit(BAD)] * 5)
    reply = ServerCapture(pipeline, model, retries=2).capture(TEXT, "s1")
    assert reply["status"] == "check_failed"
    rec = pipeline.store.read("request", reply["request_id"])
    assert rec["capture"]["attempts"] == 3 and len(model.calls) == 3


def test_a_form_failure_is_retried_like_any_error(pipeline):
    bad = output()
    bad["nodes"][0]["statistic"] = "median"
    model = FakeModel([_submit(bad), _submit(GOOD)])
    assert ServerCapture(pipeline, model).capture(TEXT, "s1")["status"] == "captured"
    assert _tool_answers(model)[0]["errors"][0]["part"] == "form"


def test_after_the_lookup_cap_only_submit_is_offered(pipeline):
    looks = [ChatReply(None, (call("describe_survey", {}, f"l{i}"),)) for i in range(3)]
    model = FakeModel([*looks, _submit(GOOD)])
    reply = ServerCapture(pipeline, model, max_lookups=2).capture(TEXT, "s1")
    assert reply["status"] == "captured"
    offered = [[t["function"]["name"] for t in tools] for _, tools in model.calls]
    assert len(offered[0]) == 7 and offered[2] == ["submit_record"]
    assert "No more lookups" in _tool_answers(model, 3)[-1]["error"]
    assert (
        pipeline.store.read("request", reply["request_id"])["capture"]["lookups"] == 2
    )


def test_a_model_that_never_submits_gives_check_failed(pipeline):
    model = FakeModel([ChatReply("Here is my answer in prose.")] * 5)
    reply = ServerCapture(pipeline, model).capture(TEXT, "s1")
    assert reply["status"] == "check_failed"
    assert len(model.calls) == 3  # the first reply and two nudges
    assert model.calls[1][0][-1]["content"] == "Return the record with submit_record."


class _Broken:
    name = "broken"

    def chat(self, messages, tools):
        raise ModelError("unreachable")


def test_a_model_failure_before_any_record_is_raised(pipeline):
    with pytest.raises(ModelError, match="unreachable"):
        ServerCapture(pipeline, _Broken()).capture(TEXT, "s1")
    assert pipeline.store.by_session("s1") == []  # nothing false is stored


def test_a_model_failure_after_a_failed_record_stores_it(pipeline):
    def fail(messages, tools):
        raise ModelError("unreachable")

    model = FakeModel([_submit(BAD), fail])
    reply = ServerCapture(pipeline, model).capture(TEXT, "s1")
    assert reply["status"] == "check_failed"
    last = reply["errors"][-1]
    assert last["part"] == "model" and "unreachable" in last["message"]
    rec = pipeline.store.read("request", reply["request_id"])
    assert rec["check"]["errors"][-1]["part"] == "model"


def test_a_server_side_check_failure_does_not_ask_for_a_correction(pipeline):
    model = FakeModel([_submit(BAD)] * 5)
    reply = ServerCapture(pipeline, model, retries=2).capture(TEXT, "s1")
    assert reply["next"] == SERVER_CHECK_FAILED
    assert "corrects_request_id" not in reply["next"]


def test_unknown_tools_and_bad_lookup_arguments_are_answered(pipeline):
    model = FakeModel(
        [
            ChatReply(
                None,
                (call("drop_table", {}), call("describe_table", {"table": "nope"})),
            ),
            _submit(GOOD),
        ]
    )
    ServerCapture(pipeline, model).capture(TEXT, "s1")
    answers = _tool_answers(model)
    assert "There is no tool" in answers[0]["error"]
    assert answers[1]["error"] == "No table named 'nope'."


def test_a_follow_up_shows_the_earlier_nodes(pipeline):
    first = ServerCapture(pipeline, FakeModel([_submit(GOOD)])).capture(TEXT, "s1")
    patch = {
        "patch": [
            {
                "op": "set_group_by",
                "node": "*",
                "group_by": [{"table": "fmli", "column": "state"}],
            }
        ]
    }
    model = FakeModel([_submit(patch)])
    reply = ServerCapture(pipeline, model).capture(
        "Now by state.", "s1", follows_request_id=first["request_id"]
    )
    assert reply["status"] == "captured"
    assert first["request_id"] in model.calls[0][0][0]["content"]
    assert '"totexpcq"' in model.calls[0][0][0]["content"]
