"""The Databricks serving endpoint, as the server-side model (D21).

A narrow client, in the spirit of `ce_mcp_server.utils.fetch_json`: one fixed
workspace host from settings, one fixed path per endpoint, no redirects, no
proxy settings from the environment, a timeout and a cap on the answer's size.
Failures become `ModelError` with no body or header in the message.

The host check and the credentials are in `workspace.py`, shared with the SQL
warehouse client. No key is stored by this project.

Not yet run against a workspace: which models the workspace offers, and
whether its endpoint takes tools, are still to be confirmed (Plan 4, build
step 9). It is tested against a mock of the chat-completions answer.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable

import httpx

from ce_capture import workspace
from ce_capture.config import CaptureSettings
from ce_capture.model import ChatReply, ModelError, ToolCall

MAX_RESPONSE_BYTES = 2_000_000


def workspace_host(value: str | None) -> str:
    """A workspace URL reduced to https://host, or ModelError."""
    return workspace.workspace_host(value, ModelError)


def token_source(
    host: str, settings: CaptureSettings, client: httpx.Client
) -> Callable[[], str]:
    return workspace.token_source(host, settings.databricks_profile, client, ModelError)


class DatabricksEndpoint:
    """A Databricks serving endpoint answering chat completions."""

    def __init__(
        self,
        endpoint: str,
        settings: CaptureSettings | None = None,
        client: httpx.Client | None = None,
        token: Callable[[], str] | None = None,
    ) -> None:
        settings = settings or CaptureSettings()
        if not endpoint or "/" in endpoint or endpoint.startswith("."):
            raise ModelError("Not a serving endpoint name.")
        self.name = endpoint
        self.host = workspace_host(
            settings.databricks_host or os.getenv("DATABRICKS_HOST")
        )
        self.url = f"{self.host}/serving-endpoints/{endpoint}/invocations"
        self.max_tokens = settings.max_tokens
        self.client = client or httpx.Client(
            timeout=settings.model_timeout, follow_redirects=False, trust_env=False
        )
        self.token = token or token_source(self.host, settings, self.client)

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatReply:
        payload = {"messages": messages, "tools": tools, "max_tokens": self.max_tokens}
        try:
            r = self.client.post(
                self.url,
                json=payload,
                headers={"Authorization": f"Bearer {self.token()}"},
            )
        except httpx.HTTPError:
            raise ModelError(
                f"The endpoint {self.name} could not be reached."
            ) from None
        if r.status_code != 200:
            raise ModelError(f"The endpoint {self.name} answered HTTP {r.status_code}.")
        if len(r.content) > MAX_RESPONSE_BYTES:
            raise ModelError(f"The endpoint {self.name} answered with too much data.")
        try:
            body = r.json()
            message = body["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError(
                f"The endpoint {self.name} answered in an unknown form."
            ) from None
        calls = []
        for c in message.get("tool_calls") or []:
            fn = c.get("function") or {}
            raw = fn.get("arguments")
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                args = raw  # kept as text: the check reports it as a form error
            calls.append(
                ToolCall(c.get("id") or uuid.uuid4().hex, fn.get("name", ""), args)
            )
        content = message.get("content")
        if isinstance(content, list):  # some models answer in content blocks
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        return ChatReply(
            content, tuple(calls), body.get("model"), body.get("usage") or {}
        )
