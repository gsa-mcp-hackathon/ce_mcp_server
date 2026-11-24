"""`evaluate_request` and `run_request`, called the way a client calls them."""

import pytest
from fastmcp import Client

from ce_mcp_server import measures as holder
from ce_mcp_server.app import mcp
from tests.measures.conftest import captured, estimate, tiny  # noqa: F401

OUTPUT = {
    "nodes": [estimate()],
    "answers": [{"asks": "average spending by region", "node": "n1"}],
    "ambiguities": [],
    "unresolved": [],
}
TEXT = "In 2025, as released, average spending by region."


@pytest.fixture
def processor(tiny):  # noqa: F811
    holder.set_processor(tiny.processor)
    yield tiny.processor
    holder.set_processor(None)


async def _call(tool, **arguments):
    async with Client(mcp) as client:
        return (await client.call_tool(tool, arguments)).data


async def test_the_tools_are_annotated_as_writing_one_record():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in ("evaluate_request", "run_request"):
        tool = tools[name]
        assert tool.annotations.read_only_hint is False
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is False
        assert "Show the `reply` text to the user as written" in tool.description
        for prop in tool.input_schema["properties"].values():
            assert prop.get("description")


async def test_evaluate_then_run(processor):
    rid = captured(processor.ctx, processor.store, TEXT, OUTPUT)
    judged = await _call("evaluate_request", request_id=rid)
    assert judged["outcome"]["mode"] == "evaluate"
    assert judged["outcome"]["nodes"][0]["verdict"] == "warn"
    assert "Will compute: the mean of" in judged["reply"]
    assert "reply" not in judged["outcome"]
    ran = await _call("run_request", request_id=rid)
    groups = ran["outcome"]["nodes"][0]["result"]["groups"]
    assert {g["group"]["region"] for g in groups} == {"1", "2", "C"}
    assert "Computed:" in ran["reply"] and "Result:" in ran["reply"]
    stored = processor.store.read("outcome", ran["outcome"]["outcome_id"])
    assert stored["reply"] == ran["reply"]


async def test_an_unknown_or_unprocessable_request_is_answered(processor):
    answer = await _call("evaluate_request", request_id="req-nothing")
    assert answer["stored"] is False and "No request record" in answer["error"]
