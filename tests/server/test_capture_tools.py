"""The capture tools, called the way a client calls them."""

import pytest
from fastmcp import Client, FastMCP

from ce_capture.config import CaptureSettings
from ce_capture.context import CaptureContext
from ce_capture.pipeline import CapturePipeline
from ce_capture.storage import JsonFileStore
from ce_mcp_server import capture as holder
from ce_mcp_server.app import mcp
from ce_mcp_server.tools import register_tools
from tests.capture.conftest import TEXT, node, output


@pytest.fixture
def pipeline(meta_service, tmp_path):
    p = CapturePipeline(
        CaptureContext.from_service(meta_service), JsonFileStore(tmp_path)
    )
    holder.set_pipeline(p)
    yield p
    holder.set_pipeline(None)


async def _call(tool, **arguments):
    async with Client(mcp) as client:
        return (await client.call_tool(tool, arguments)).data


async def _names(server) -> set[str]:
    async with Client(server) as client:
        return {t.name for t in await client.list_tools()}


async def test_one_capture_path_per_deployment(monkeypatch):
    lookups = {"describe_survey", "list_datasets", "describe_table", "search_variables",
               "describe_variable", "list_geographies",
               "evaluate_request", "run_request"}  # fmt: skip
    for env, extra in [
        ({}, {"capture_guide", "submit_request_record"}),
        ({"CE_CAPTURE_ENDPOINT": "capture-claude"}, {"capture_request"}),
        (
            {"CE_CAPTURE_ENDPOINT": "capture-claude", "CE_CAPTURE_BOTH_PATHS": "true"},
            {"capture_request", "capture_guide", "submit_request_record"},
        ),
    ]:
        for name in ("CE_CAPTURE_ENDPOINT", "CE_CAPTURE_BOTH_PATHS"):
            monkeypatch.delenv(name, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        server = FastMCP("t")
        register_tools(server)
        assert await _names(server) == lookups | extra


async def test_the_capture_tools_write_records():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    submit = tools["submit_request_record"]
    assert submit.annotations.read_only_hint is False
    assert submit.annotations.destructive_hint is False
    assert submit.input_schema["required"] == ["user_text", "session_id", "record"]
    assert tools["capture_guide"].annotations.read_only_hint is True


async def test_capture_guide(pipeline):
    guide = await _call("capture_guide")
    assert guide["instructions_version"] == "1.1"
    assert guide["after_capture_version"] == "1.0"
    assert "A block is an answer" in guide["after_capture"]
    assert "<!--" not in guide["after_capture"]
    assert guide["tables"] == ["fmli", "mtbi"] and guide["loaded_years"] == [2025]
    assert "Names come from lookup answers" in guide["instructions"]
    assert "record_schema" not in guide
    assert (
        "nodes"
        in (await _call("capture_guide", include_schema=True))["record_schema"][
            "properties"
        ]
    )


async def test_submit_a_record_and_correct_it(pipeline):
    bad = output(node(measure={"table": "fmli", "column": "totexpq"}))
    first = await _call(
        "submit_request_record",
        user_text=TEXT,
        session_id="s1",
        record=bad,
        model="client-x",
    )
    assert first["status"] == "check_failed"
    fixed = await _call(
        "submit_request_record",
        user_text=TEXT,
        session_id="s1",
        record=output(),
        corrects_request_id=first["request_id"],
    )
    assert fixed["status"] == "captured" and fixed["resolved_id"]
    rec = pipeline.store.read("request", fixed["request_id"])
    assert rec["capture"]["attempts"] == 2
    assert (
        pipeline.store.read("request", first["request_id"])["capture"]["model"]
        == "client-x"
    )


async def test_a_refused_link_is_an_error_and_stores_nothing(pipeline):
    answer = await _call(
        "submit_request_record",
        user_text=TEXT,
        session_id="s1",
        record=output(),
        follows_request_id="req-missing",
    )
    assert answer == {
        "error": "follows_request_id: no request record 'req-missing'.",
        "stored": False,
    }
    assert pipeline.store.by_session("s1") == []


async def test_a_schema_violation_is_refused_at_the_tool(pipeline):
    bad = output()
    bad["nodes"][0]["statistic"] = "median"
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="statistic|validation"):
            await client.call_tool(
                "submit_request_record",
                {"user_text": TEXT, "session_id": "s1", "record": bad},
            )
    assert pipeline.store.by_session("s1") == []


def test_settings_defaults():
    s = CaptureSettings()
    assert (s.max_lookups, s.retries, str(s.records_dir)) == (12, 2, "data/records")


async def test_the_guide_covers_the_three_things_models_got_wrong(pipeline):
    """From the first evaluation (eval/baseline): households read as
    interviews, a near thing captured instead of asked about, and a block
    worked around."""
    guide = await _call("capture_guide")
    text = " ".join(guide["instructions"].split())
    assert "A household is a consumer unit, however the user puts it" in text
    assert "Use `interview` only when the user asks for interviews themselves" in text
    assert "Near is not the same" in text and "`ask_user: true`" in text
    assert "search once more with another word" in text
    after = " ".join(guide["after_capture"].split())
    assert "Do not capture a different request in its place" in after
    assert "Do not work out further ones from them" in after
    assert "after_capture" in guide["summary"]


async def test_the_server_side_model_reads_only_how_to_write_the_record():
    """The server-side capture model cannot run a request, so the guidance for
    after capture is not in its prompt."""
    from ce_capture.guide import after_capture, instructions

    _, text = instructions()
    assert "run_request" not in text and "A block is an answer" not in text
    version, after = after_capture()
    assert version == "1.0" and "run_request" in after


async def test_the_processing_tools_say_a_block_is_the_answer():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    for name in ("run_request", "evaluate_request"):
        text = " ".join(tools[name].description.split())
        assert "A statistic the server blocks is answered by its reason" in text
        assert "Do not work out figures of your own" in text
    search = " ".join(tools["search_variables"].description.split())
    assert "`lacks`" in search and "search again with another word" in search
