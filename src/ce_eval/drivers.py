"""Putting a conversation to a model that can call the server's tools.

A driver takes the user's messages of one conversation, in order, and returns
a transcript: every tool call with what the server returned, and the model's
final answer to each message. Two drivers:

- `LoopDriver`: any chat model behind `ce_capture.model.ModelClient` (the
  Databricks endpoint, or a scripted model), with the server in this process.
- `ClaudeCliDriver`: a headless Claude Code session (`claude -p`) connected to
  the real server over stdio, with no other tool, no project files and no
  user settings: what a client model sees and nothing more.

Both write records to a folder of the caller's choosing, so the harness can
read what the model stored.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ce_capture.model import ModelClient, ModelError
from ce_eval.offline import Env
from ce_eval.trajectory import LOOKUPS, ToolUse, Transcript, Turn

PROMPTS_DIR = Path("eval/prompts")
DEFAULT_PROMPT = "minimal"
MAX_MODEL_CALLS = 40  # per turn: a guard, no request needs this many
SERVER_NAME = "ce"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"


class DriverError(RuntimeError):
    """The driver could not run the conversation."""


class Driver(Protocol):
    name: str

    def converse(self, messages: list[str], env: Env) -> Transcript: ...


def load_prompt(name: str = DEFAULT_PROMPT, folder: Path = PROMPTS_DIR) -> str:
    path = Path(folder) / f"{name}.md"
    if not path.exists():
        raise DriverError(f"no agent prompt {path}")
    return path.read_text().strip()


def _parsed(text: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


# ── the server in this process ───────────────────────────────────────────────


@contextmanager
def serving(env: Env) -> Iterator[None]:
    """Point the process's server at an environment for the time of a run."""
    from ce_mcp_server import capture as capture_holder
    from ce_mcp_server import measures as measures_holder
    from ce_mcp_server import service as service_holder

    if env.ctx.service is not None:
        service_holder.set_service(env.ctx.service)
    capture_holder.set_pipeline(env.pipeline)
    measures_holder.set_processor(env.processor)
    try:
        yield
    finally:
        service_holder.set_service(None)
        capture_holder.set_pipeline(None)
        measures_holder.set_processor(None)


class LoopDriver:
    """A chat model in a tool loop, with the server in this process."""

    def __init__(
        self,
        model: ModelClient,
        prompt: str,
        disallow: tuple[str, ...] = (),
        max_lookups: int | None = None,
    ) -> None:
        self.model = model
        self.name = model.name
        self.prompt = prompt
        self.disallow = set(disallow)
        self.max_lookups = max_lookups

    def converse(self, messages: list[str], env: Env) -> Transcript:
        with serving(env):
            return asyncio.run(self._converse(messages))

    async def _converse(self, user_messages: list[str]) -> Transcript:
        from fastmcp import Client

        from ce_mcp_server.app import mcp

        transcript = Transcript(model=self.name)
        async with Client(mcp) as client:
            offered = [
                t for t in await client.list_tools() if t.name not in self.disallow
            ]
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.input_schema,
                    },
                }
                for t in offered
            ]
            names = {t.name for t in offered}
            history: list[dict] = [{"role": "system", "content": self.prompt}]
            lookups = 0
            for text in user_messages:
                turn = Turn(user=text)
                started = time.time()
                history.append({"role": "user", "content": text})
                for _ in range(MAX_MODEL_CALLS):
                    capped = (
                        self.max_lookups is not None and lookups >= self.max_lookups
                    )
                    shown = [
                        t
                        for t in tools
                        if not (capped and t["function"]["name"] in LOOKUPS)
                    ]
                    try:
                        reply = self.model.chat(history, shown)
                    except ModelError as exc:
                        turn.error = str(exc)
                        break
                    for key, value in (reply.usage or {}).items():
                        if isinstance(value, int):
                            turn.usage[key] = turn.usage.get(key, 0) + value
                    history.append(reply.as_message())
                    if not reply.tool_calls:
                        turn.answer = reply.content or ""
                        break
                    for call in reply.tool_calls:
                        args = (
                            call.arguments if isinstance(call.arguments, dict) else {}
                        )
                        if call.name not in names:
                            result: Any = {"error": f"There is no tool {call.name!r}."}
                        else:
                            try:
                                answer = await client.call_tool(
                                    call.name, args, raise_on_error=False
                                )
                                result = answer.data
                                if result is None:
                                    result = _parsed(
                                        "".join(
                                            getattr(c, "text", "")
                                            for c in answer.content
                                        )
                                    )
                            except Exception as exc:  # a malformed call
                                result = {"error": str(exc)}
                        lookups += call.name in LOOKUPS
                        turn.calls.append(ToolUse(call.name, args, result))
                        history.append(
                            {
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": json.dumps(result, default=str),
                            }
                        )
                else:
                    turn.error = "the model never gave a final answer"
                turn.seconds = time.time() - started
                transcript.turns.append(turn)
        return transcript


# ── a headless Claude Code session ───────────────────────────────────────────


@dataclass
class ServerPaths:
    """Where the stdio server of a run reads its sidecars and data."""

    sidecar_dir: Path
    data_dir: Path | None


class ClaudeCliDriver:
    """`claude -p` with the real server over stdio and nothing else."""

    parallel = True  # each conversation has its own server process and store

    def __init__(
        self,
        model: str,
        prompt: str,
        paths: ServerPaths,
        disallow: tuple[str, ...] = (),
        timeout: float = 600.0,
        executable: str = "claude",
    ) -> None:
        self.model = model
        self.name = f"claude-cli:{model}"
        self.prompt = prompt
        self.paths = paths
        self.disallow = tuple(disallow)
        self.timeout = timeout
        self.executable = executable

    def _config(self, records_dir: Path, folder: Path) -> Path:
        env = {
            "CE_CAPTURE_RECORDS_DIR": str(records_dir),
            "CE_META_SIDECAR_DIR": str(self.paths.sidecar_dir.resolve()),
            "CE_META_DATA_DIR": str(
                (self.paths.data_dir or folder / "no-data").resolve()
            ),
        }
        config = {
            "mcpServers": {
                SERVER_NAME: {
                    "command": sys.executable,
                    "args": ["-m", "ce_mcp_server.app"],
                    "env": env,
                }
            }
        }
        path = folder / "mcp.json"
        path.write_text(json.dumps(config))
        return path

    def command(
        self, message: str, config: Path, session: str, first: bool
    ) -> list[str]:
        cmd = [
            self.executable,
            "-p",
            message,
            "--model",
            self.model,
            "--system-prompt",
            self.prompt,
            "--mcp-config",
            str(config),
            "--strict-mcp-config",
            "--tools",
            "",
            "--allowedTools",
            f"mcp__{SERVER_NAME}",
            "--setting-sources",
            "",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if self.disallow:
            cmd += ["--disallowedTools", *[TOOL_PREFIX + t for t in self.disallow]]
        cmd += ["--session-id", session] if first else ["--resume", session]
        return cmd

    def converse(self, messages: list[str], env: Env) -> Transcript:
        transcript = Transcript(model=self.name, cost_usd=0.0)
        session = str(uuid.uuid4())
        with tempfile.TemporaryDirectory(prefix="ce-eval-cli-") as tmp:
            folder = Path(tmp)
            config = self._config(env.store.folder, folder)
            for i, text in enumerate(messages):
                started = time.time()
                try:
                    out = subprocess.run(
                        self.command(text, config, session, first=i == 0),
                        capture_output=True,
                        text=True,
                        stdin=subprocess.DEVNULL,
                        timeout=self.timeout,
                        cwd=folder,  # an empty folder: no project files to read
                        env=_clean_env(),
                    )
                    turn, cost = parse_stream(text, out.stdout)
                    if out.returncode != 0 and not turn.answer:
                        turn.error = (out.stderr or out.stdout)[-400:].strip()
                except subprocess.TimeoutExpired:
                    turn, cost = Turn(user=text, error="timed out"), 0.0
                except OSError as exc:
                    raise DriverError(
                        f"could not start {self.executable}: {exc}"
                    ) from None
                turn.seconds = time.time() - started
                transcript.cost_usd = (transcript.cost_usd or 0.0) + cost
                transcript.turns.append(turn)
                if turn.error:
                    break
        return transcript


def _clean_env() -> dict[str, str]:
    """The environment for a nested session: this session's own markers and
    the project's server settings are left out."""
    drop = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")
    return {
        k: v
        for k, v in os.environ.items()
        if k not in drop and not k.startswith(("CE_", "MCP_"))
    }


def parse_stream(user: str, stdout: str) -> tuple[Turn, float]:
    """One turn from the stream-json output of `claude -p`."""
    turn = Turn(user=user)
    pending: dict[str, ToolUse] = {}
    cost = 0.0
    texts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")
        content = (event.get("message") or {}).get("content")
        if kind == "assistant" and isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    name = str(block.get("name", "")).removeprefix(TOOL_PREFIX)
                    use = ToolUse(name, block.get("input") or {}, None)
                    pending[block.get("id")] = use
                    turn.calls.append(use)
                elif block.get("type") == "text" and block.get("text"):
                    texts.append(block["text"])
        elif kind == "user" and isinstance(content, list):
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                use = pending.get(block.get("tool_use_id"))
                if use is None:
                    continue
                body = block.get("content")
                if isinstance(body, list):
                    body = "".join(
                        b.get("text", "") for b in body if isinstance(b, dict)
                    )
                use.result = _parsed(body)
        elif kind == "result":
            turn.answer = event.get("result") or ""
            cost = float(event.get("total_cost_usd") or 0.0)
            usage = event.get("usage") or {}
            turn.usage = {k: v for k, v in usage.items() if isinstance(v, int)}
            if event.get("is_error"):
                turn.error = str(event.get("result") or event.get("subtype"))
    if not turn.answer and texts:
        turn.answer = texts[-1]
    return turn, cost
