"""The Databricks endpoint client (`ce_capture.databricks`), against a mock."""

import json

import httpx
import pytest

from ce_capture.config import CaptureSettings
from ce_capture.databricks import DatabricksEndpoint, token_source, workspace_host
from ce_capture.model import ModelError

HOST = "https://adb-1.2.azuredatabricks.net"


def _endpoint(handler, **settings):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    s = CaptureSettings(databricks_host=HOST, **settings)
    return DatabricksEndpoint("capture-claude", s, client=client, token=lambda: "tok")


def _completion(message, model="claude-test"):
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": message}],
            "usage": {"total_tokens": 42},
        },
    )


def test_the_request_and_a_tool_call_reply():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _completion(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "describe_survey", "arguments": "{}"},
                    },
                    {
                        "id": "c2",
                        "type": "function",
                        "function": {"name": "submit_record", "arguments": "{not json"},
                    },
                ],
            }
        )

    reply = _endpoint(handler).chat(
        [{"role": "user", "content": "hi"}], [{"type": "function"}]
    )
    assert seen["url"] == f"{HOST}/serving-endpoints/capture-claude/invocations"
    assert seen["auth"] == "Bearer tok"
    assert (
        seen["body"]["messages"][0]["content"] == "hi"
        and seen["body"]["max_tokens"] == 4096
    )
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("c1", "describe_survey", {}),
        ("c2", "submit_record", "{not json"),  # kept as text: a form error later
    ]
    assert (reply.model, reply.usage["total_tokens"]) == ("claude-test", 42)


def test_content_blocks_are_joined():
    reply = _endpoint(
        lambda r: _completion(
            {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        )
    ).chat([], [])
    assert reply.content == "ab" and reply.tool_calls == ()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401, text="secret detail"), "answered HTTP 401"),
        (
            httpx.Response(302, headers={"location": "https://evil.example"}),
            "answered HTTP 302",
        ),
        (httpx.Response(200, text="not json"), "unknown form"),
        (httpx.Response(200, json={"choices": []}), "unknown form"),
    ],
)
def test_failures_are_sanitized(response, message):
    with pytest.raises(ModelError, match=message) as exc:
        _endpoint(lambda r: response).chat([], [])
    assert "secret" not in str(exc.value)


def test_an_unreachable_endpoint():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(ModelError, match="could not be reached"):
        _endpoint(handler).chat([], [])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("adb-1.2.azuredatabricks.net", "https://adb-1.2.azuredatabricks.net"),
        ("https://adb-1.2.azuredatabricks.net/", "https://adb-1.2.azuredatabricks.net"),
        ("https://host:8443", "https://host:8443"),
    ],
)
def test_workspace_host(value, expected):
    assert workspace_host(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "http://host",
        "https://user:pw@host",
        "https://host/path",
        "https://host?q=1",
    ],
)
def test_a_bad_workspace_host_is_refused(value):
    with pytest.raises(ModelError):
        workspace_host(value)


def test_a_bad_endpoint_name_is_refused():
    with pytest.raises(ModelError):
        DatabricksEndpoint(
            "../x", CaptureSettings(databricks_host=HOST), token=lambda: "t"
        )


def test_token_from_the_environment(monkeypatch):
    monkeypatch.setenv("DATABRICKS_TOKEN", "env-token")
    assert token_source(HOST, CaptureSettings(), httpx.Client())() == "env-token"


def test_token_for_a_service_principal_is_cached(monkeypatch):
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "id")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "secret")
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == f"{HOST}/oidc/v1/token"
        return httpx.Response(
            200, json={"access_token": "sp-token", "expires_in": 3600}
        )

    source = token_source(
        HOST, CaptureSettings(), httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert source() == "sp-token" and source() == "sp-token"
    assert len(calls) == 1
    assert b"client_credentials" in calls[0].content


def test_no_credentials_at_all(monkeypatch):
    for name in (
        "DATABRICKS_TOKEN",
        "DATABRICKS_CLIENT_ID",
        "DATABRICKS_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATH", "")  # no databricks CLI
    with pytest.raises(ModelError, match="No Databricks credentials"):
        token_source(HOST, CaptureSettings(), httpx.Client())()
