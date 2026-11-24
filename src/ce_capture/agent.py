"""The server-side capture loop: a model with lookups and retries (D19, D20).

The model reads the capture instructions, calls lookup tools to find tables
and columns, and returns its record through `submit_record`. Code prepares the
record exactly as on the client-model path; when the check finds errors they go
back to the model, at most `retries` times (D20). After `max_lookups` lookups
only `submit_record` is offered. Only the final record is stored. If the model
cannot be reached before it submits anything, the error is raised and nothing
is stored.
"""

from __future__ import annotations

import json
import logging

from ce_capture.guide import instructions
from ce_capture.lookups import SUBMIT, as_content, run_lookup, tool_list
from ce_capture.model import ModelClient, ModelError
from ce_capture.pipeline import CapturePipeline, Draft, Submission, _error
from ce_capture.storage import new_request_id

log = logging.getLogger(__name__)

MAX_TURNS = 30  # a guard: no capture needs this many model calls
MAX_NUDGES = 2  # replies with no tool call before the loop gives up


def _earlier_note(follows: dict | None, earlier_text: str | None) -> str:
    if follows is not None:
        nodes = json.dumps(follows["nodes"], ensure_ascii=False)
        return (
            f"\n\nThis message follows request {follows['request_id']}, which asked: "
            f"{follows['source']['user_text']!r}. Its nodes were: {nodes}. Write a "
            "patch of changes to them, not the nodes again."
        )
    if earlier_text:
        return (
            f"\n\nThis message answers a question about an earlier request, which "
            f"asked: {earlier_text!r}. Write the whole request again, with the answer "
            "taken into account. Evidence may come from either message."
        )
    return ""


class ServerCapture:
    def __init__(
        self,
        pipeline: CapturePipeline,
        model: ModelClient,
        max_lookups: int = 12,
        retries: int = 2,
    ) -> None:
        self.pipeline = pipeline
        self.model = model
        self.max_lookups = max_lookups
        self.retries = retries

    def capture(
        self,
        user_text: str,
        session_id: str,
        follows_request_id: str | None = None,
        answers_request_id: str | None = None,
        user_ref: str | None = None,
    ) -> dict:
        sub = Submission(
            user_text=user_text,
            session_id=session_id,
            output=None,
            path="server_model",
            model=self.model.name,
            follows_request_id=follows_request_id,
            answers_request_id=answers_request_id,
            user_ref=user_ref,
        )
        sub, follows, earlier_text = self.pipeline.resolve_links(sub)
        service = self.pipeline.ctx.service
        request_id = new_request_id()
        _, text = instructions()
        messages: list[dict] = [
            {"role": "system", "content": text + _earlier_note(follows, earlier_text)},
            {"role": "user", "content": user_text},
        ]
        lookups = attempts = nudges = 0
        draft: Draft | None = None

        for _ in range(MAX_TURNS):
            may_look = lookups < self.max_lookups and service is not None
            try:
                reply = self.model.chat(messages, tool_list(include_lookups=may_look))
            except ModelError as exc:
                log.warning("capture model failed: %s", exc)
                if draft is None:
                    raise  # nothing was submitted, so there is no record to store
                draft.errors.append(
                    _error(
                        "model",
                        "The capture model failed before the record was corrected: "
                        f"{exc}",
                    )
                )
                break
            if reply.model:
                sub.model = reply.model
            messages.append(reply.as_message())
            if not reply.tool_calls:
                nudges += 1
                if nudges > MAX_NUDGES:
                    break
                messages.append(
                    {"role": "user", "content": f"Return the record with {SUBMIT}."}
                )
                continue
            for call in reply.tool_calls:
                if call.name == SUBMIT:
                    attempts += 1
                    sub.output = call.arguments
                    draft = self.pipeline.prepare(
                        sub, follows, earlier_text, attempts, lookups, request_id
                    )
                    if not draft.errors or attempts > self.retries:
                        return self.pipeline.finish(draft)
                    answer = {
                        "passed": False,
                        "errors": draft.errors,
                        "next": "Correct the record and call submit_record again.",
                    }
                elif not may_look:
                    answer = {
                        "error": f"No more lookups: return the record with {SUBMIT}."
                    }
                else:
                    lookups += 1
                    answer = run_lookup(service, call.name, call.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": as_content(answer),
                    }
                )

        # The model gave up, failed, or never passed the check.
        if draft is None:
            sub.output = {
                "nodes": [],
                "reason_code": "other",
                "reason": "The model returned no record.",
            }
            draft = self.pipeline.prepare(
                sub, follows, earlier_text, max(attempts, 1), lookups, request_id
            )
            draft.record["capture"]["status"] = draft.status = "check_failed"
        return self.pipeline.finish(draft)
