"""The drivers: the tool loop, and the headless Claude session (parsing and
the command line only; no session is started here)."""

import json
from pathlib import Path

import pytest

from ce_capture.model import ChatReply, FakeModel, call
from ce_eval import offline
from ce_eval.drivers import (
    ClaudeCliDriver,
    DriverError,
    LoopDriver,
    ServerPaths,
    load_prompt,
    parse_stream,
)
from tests.eval.conftest import ROOT


def test_the_agent_prompt_loads():
    text = load_prompt("minimal", ROOT / "eval" / "prompts")
    assert "CE server" in text and "CLAUDE" not in text
    with pytest.raises(DriverError):
        load_prompt("nothing", ROOT / "eval" / "prompts")


def test_the_loop_runs_tools_and_keeps_the_conversation(tiny):
    model = FakeModel(
        [
            ChatReply(
                None,
                (call("describe_survey", {}, "c1"), call("no_such_tool", {}, "c2")),
            ),
            ChatReply("First answer."),
            ChatReply("Second answer."),
        ]
    )
    env = offline.make_env(tiny.ctx, [2025], tiny.data_dir)
    transcript = LoopDriver(model, "PROMPT").converse(["one", "two"], env)
    first, second = transcript.turns
    assert first.answer == "First answer." and second.answer == "Second answer."
    assert first.calls[0].data["weight"]["value"]["column"] == "finlwt21"
    assert "no tool" in first.calls[1].result["error"]
    last_call = model.calls[-1][0]
    assert last_call[0] == {"role": "system", "content": "PROMPT"}
    assert [m["content"] for m in last_call if m["role"] == "user"] == ["one", "two"]
    offered = {t["function"]["name"] for t in model.calls[0][1]}
    assert {"run_request", "submit_request_record", "describe_survey"} <= offered


def test_tools_can_be_withheld_and_lookups_capped(tiny):
    look = ChatReply(None, (call("describe_survey", {}),))
    model = FakeModel([look, look, ChatReply("done")])
    env = offline.make_env(tiny.ctx, [2025], tiny.data_dir)
    LoopDriver(model, "p", disallow=("capture_guide",), max_lookups=1).converse(
        ["q"], env
    )
    names = [{t["function"]["name"] for t in tools} for _, tools in model.calls]
    assert "capture_guide" not in names[0] and "describe_survey" in names[0]
    assert "describe_survey" not in names[1] and "submit_request_record" in names[1]


def test_a_model_that_never_answers_is_an_error(tiny):
    model = FakeModel([ChatReply(None, (call("describe_survey", {}),))] * 60)
    env = offline.make_env(tiny.ctx, [2025], tiny.data_dir)
    turn = LoopDriver(model, "p").converse(["q"], env).turns[0]
    assert turn.error == "the model never gave a final answer"


STREAM = [
    {"type": "system", "subtype": "init"},
    {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Let me look."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "mcp__ce__submit_request_record",
                    "input": {
                        "user_text": "q",
                        "session_id": "s",
                        "record": {"nodes": []},
                    },
                },
            ]
        },
    },
    {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"status": "captured", "request_id": "req-1"}
                            ),
                        }
                    ],
                }
            ]
        },
    },
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "mcp__ce__run_request",
                    "input": {"request_id": "req-1"},
                }
            ]
        },
    },
    {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": "plain text, not JSON",
                }
            ]
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "result": "The answer is $5.",
        "total_cost_usd": 0.02,
        "usage": {"input_tokens": 10, "output_tokens": 4},
    },
]


def test_a_stream_is_read_into_a_turn():
    text = "noise\n" + "\n".join(json.dumps(e) for e in STREAM) + "\n{broken"
    turn, cost = parse_stream("q", text)
    assert [c.name for c in turn.calls] == ["submit_request_record", "run_request"]
    assert turn.calls[0].data == {"status": "captured", "request_id": "req-1"}
    assert turn.calls[1].result == "plain text, not JSON" and turn.calls[1].data == {}
    assert turn.answer == "The answer is $5." and cost == 0.02
    assert turn.usage == {"input_tokens": 10, "output_tokens": 4}
    assert turn.last_request.data["request_id"] == "req-1"


def test_a_stream_with_no_result_keeps_the_last_text():
    turn, cost = parse_stream("q", json.dumps(STREAM[1]))
    assert turn.answer == "Let me look." and cost == 0.0


def test_the_session_gets_the_server_and_nothing_else(tmp_path):
    driver = ClaudeCliDriver(
        "sonnet",
        "PROMPT",
        ServerPaths(ROOT / "sidecars", None),
        disallow=("capture_guide",),
    )
    config = driver._config(tmp_path / "records", tmp_path)
    server = json.loads(Path(config).read_text())["mcpServers"]["ce"]
    assert server["args"] == ["-m", "ce_mcp_server.app"]
    assert server["env"]["CE_CAPTURE_RECORDS_DIR"] == str(tmp_path / "records")
    assert Path(server["env"]["CE_META_SIDECAR_DIR"]).is_absolute()
    first = driver.command("a question", config, "sid", first=True)
    assert first[:3] == ["claude", "-p", "a question"]
    assert first[first.index("--tools") + 1] == ""  # no built-in tool
    assert "--strict-mcp-config" in first
    assert (
        first[first.index("--setting-sources") + 1] == ""
    )  # no user or project settings
    assert first[first.index("--system-prompt") + 1] == "PROMPT"
    assert first[first.index("--disallowedTools") + 1] == "mcp__ce__capture_guide"
    assert first[-2:] == ["--session-id", "sid"]
    assert driver.command("next", config, "sid", first=False)[-2:] == [
        "--resume",
        "sid",
    ]
    assert driver.parallel and driver.name == "claude-cli:sonnet"
