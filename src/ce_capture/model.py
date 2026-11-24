"""The model the server calls, behind one interface (Plan 4, the server-side path).

Two implementations: the Databricks serving endpoint (`databricks.py`) and
`FakeModel`, which plays back prepared replies for tests. Messages and tools use
the chat-completions shape that Databricks serving endpoints accept.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol


class ModelError(RuntimeError):
    """The model could not be reached, or answered in an unreadable form."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: object  # the parsed arguments; a str when they were not JSON


@dataclass(frozen=True)
class ChatReply:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    model: str | None = None
    usage: dict = field(default_factory=dict)

    def as_message(self) -> dict:
        """The assistant message to append to the conversation."""
        message: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": c.arguments
                        if isinstance(c.arguments, str)
                        else json.dumps(c.arguments),
                    },
                }
                for c in self.tool_calls
            ]
        return message


class ModelClient(Protocol):
    name: str

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatReply: ...


Step = ChatReply | Callable[[list[dict], list[dict]], ChatReply]


class FakeModel:
    """Plays back prepared replies, one per call, and keeps what it was sent."""

    def __init__(self, steps: Sequence[Step], name: str = "fake-model") -> None:
        self.steps = list(steps)
        self.name = name
        self.calls: list[tuple[list[dict], list[dict]]] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatReply:
        self.calls.append(([dict(m) for m in messages], list(tools)))
        if not self.steps:
            return ChatReply(content="I have nothing more to add.")
        step = self.steps.pop(0)
        return step(messages, tools) if callable(step) else step


def call(name: str, arguments: object, call_id: str | None = None) -> ToolCall:
    """A tool call, for building fake replies."""
    return ToolCall(call_id or f"call-{name}", name, arguments)
