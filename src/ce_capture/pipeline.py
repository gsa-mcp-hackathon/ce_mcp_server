"""The capture pipeline shared by both paths (D3): model output in, records out.

A model's output goes through the same steps on the server-side path and the
client-model path: validation against the schema, the follow-up patch,
normalize, the capture check, the status (D25), storage, and derivation when
the record is captured. `prepare` runs the steps up to the status without
storing, so the server-side loop can send errors back to its model; `finish`
stores what `prepare` built.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from ce_capture.check import CheckOutcome, check_record, column_refs, form_errors
from ce_capture.context import CaptureContext
from ce_capture.derive import derive
from ce_capture.followup import PatchError, apply_patch
from ce_capture.guide import instructions
from ce_capture.normalize import normalize
from ce_capture.records import SCHEMA_VERSION, CapturePath, ModelOutput, dump
from ce_capture.storage import RecordStore, new_request_id
from ce_capture.text import words

MAX_CORRECTIONS = 2  # D28: a third correction in one chain is refused


class LinkError(ValueError):
    """A submission that names an earlier record it cannot be linked to.

    Refused without storing anything: there is no record to keep.
    """


@dataclass
class Submission:
    user_text: str
    session_id: str
    output: Any  # what the model wrote, not yet validated
    path: CapturePath = "client_model"
    model: str | None = None
    follows_request_id: str | None = None
    answers_request_id: str | None = None
    corrects_request_id: str | None = None
    turn: int | None = None
    user_ref: str | None = None


@dataclass
class Draft:
    record: dict
    outcome: CheckOutcome
    status: str
    questions: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[dict]:
        return self.record["check"]["errors"]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _chain_text(earlier: dict) -> str:
    """The user's words so far in a chain of linked requests: what the earlier
    record could quote, then its own message. A follow-up to a follow-up may
    still quote the first message ("as released" said once holds for all)."""
    source = earlier.get("source") or {}
    parts = [source.get("earlier_text"), source.get("user_text")]
    return "\n".join(p for p in parts if p)


class CapturePipeline:
    def __init__(self, ctx: CaptureContext, store: RecordStore) -> None:
        self.ctx = ctx
        self.store = store

    # ── links between records ────────────────────────────────────────────────

    def _earlier(self, record_id: str, session_id: str, role: str) -> dict:
        earlier = self.store.read("request", record_id)
        if earlier is None:
            raise LinkError(f"{role}: no request record {record_id!r}.")
        if (earlier.get("source") or {}).get("session_id") != session_id:
            raise LinkError(f"{role}: {record_id} belongs to another session.")
        return earlier

    def resolve_links(
        self, sub: Submission
    ) -> tuple[Submission, dict | None, str | None]:
        """Check the submission's links; return it with inherited links, the
        earlier record a patch applies to, and the earlier text."""
        sub = copy.copy(sub)
        earlier_text = None
        if sub.corrects_request_id:
            failed = self._earlier(
                sub.corrects_request_id, sub.session_id, "corrects_request_id"
            )
            if failed["capture"]["status"] != "check_failed":
                raise LinkError(
                    f"corrects_request_id: {failed['request_id']} has status "
                    f"{failed['capture']['status']}; only a check_failed record is "
                    "corrected."
                )
            if self.chain_depth(failed) >= MAX_CORRECTIONS:
                raise LinkError(
                    f"{failed['request_id']} has been corrected "
                    f"{MAX_CORRECTIONS} times "
                    "already. Tell the user the request could not be captured, and "
                    "ask them to rephrase it."
                )
            if words(failed["source"]["user_text"]) != words(sub.user_text):
                raise LinkError(
                    "corrects_request_id: a correction must carry the same user_text "
                    "as the record it corrects."
                )
            source = failed["source"]
            sub.follows_request_id = sub.follows_request_id or source.get(
                "follows_request_id"
            )
            sub.answers_request_id = sub.answers_request_id or source.get(
                "answers_request_id"
            )
        follows = None
        if sub.follows_request_id:
            follows = self._earlier(
                sub.follows_request_id, sub.session_id, "follows_request_id"
            )
            if follows["capture"]["status"] != "captured":
                raise LinkError(
                    f"follows_request_id: {follows['request_id']} has status "
                    f"{follows['capture']['status']}; only a captured request is "
                    "followed."
                )
            earlier_text = _chain_text(follows)
        if sub.answers_request_id:
            asked = self._earlier(
                sub.answers_request_id, sub.session_id, "answers_request_id"
            )
            if asked["capture"]["status"] != "needs_clarification":
                raise LinkError(
                    f"answers_request_id: {asked['request_id']} has status "
                    f"{asked['capture']['status']}; only a request that needs "
                    "clarification is answered."
                )
            earlier_text = _chain_text(asked)
        return sub, follows, earlier_text

    def chain_depth(self, record: dict) -> int:
        """How many corrections lead to this record."""
        depth = 0
        seen = {record["request_id"]}
        while (
            parent := (record.get("source") or {}).get("corrects_request_id")
        ) and depth < 10:
            record = self.store.read("request", parent) or {}
            if not record or record.get("request_id") in seen:
                break
            seen.add(record["request_id"])
            depth += 1
        return depth

    # ── building the record ──────────────────────────────────────────────────

    def prepare(
        self,
        sub: Submission,
        follows: dict | None,
        earlier_text: str | None,
        attempts: int = 1,
        lookups: int | None = None,
        request_id: str | None = None,
    ) -> Draft:
        """The record a submission makes, checked, with its status. Not stored."""
        ctx = self.ctx
        pre_errors: list[dict] = []
        submitted = None
        try:
            output = ModelOutput.model_validate(sub.output)
        except ValidationError as exc:
            output = ModelOutput()
            pre_errors = form_errors(exc)
            submitted = sub.output

        carried = None
        nodes = [dump(n) for n in output.nodes]
        answers = [dump(a) for a in output.answers]
        if output.patch is not None:
            if output.nodes:
                pre_errors.append(_error("patch", "Write nodes or a patch, not both."))
            elif follows is None:
                pre_errors.append(
                    _error(
                        "patch",
                        "A patch needs follows_request_id: the request it changes.",
                    )
                )
            else:
                try:
                    nodes, earlier_answers, carried = apply_patch(follows, output.patch)
                    answers = earlier_answers + answers
                except PatchError as exc:
                    pre_errors.append(_error("patch", str(exc)))

        nodes, fixes, flags = normalize(nodes, answers, ctx)
        tables = sorted(
            {str(ref.get("table")) for n in nodes for _, ref in column_refs(n)},
        )
        record = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id or new_request_id(),
            "captured_at": now(),
            "source": {
                "user_text": sub.user_text,
                "session_id": sub.session_id,
                "turn": sub.turn or len(self.store.by_session(sub.session_id)) + 1,
                "user_ref": sub.user_ref,
                "follows_request_id": sub.follows_request_id,
                "answers_request_id": sub.answers_request_id,
                "corrects_request_id": sub.corrects_request_id,
                "earlier_text": earlier_text,
            },
            "capture": {
                "path": sub.path,
                "agent": f"ce-capture/{instructions()[0]}",
                "model": sub.model,
                "attempts": attempts,
                "status": "check_failed",  # set below
                "reason_code": output.reason_code,
                "reason": output.reason,
                "questions": [],
                "lookups": lookups,
            },
            "sidecars": ctx.versions(tables),
            "nodes": nodes,
            "answers": answers,
            "ambiguities": [dump(a) for a in output.ambiguities],
            "unresolved": [dump(u) for u in output.unresolved],
            "carried_over": carried,
            "fixes": fixes,
            "evidence": [],
            "check": {"passed": False, "errors": [], "flags": []},
            "submitted": submitted,
        }

        if submitted is not None:
            outcome = CheckOutcome(errors=pre_errors)
        else:
            outcome = check_record(record, ctx, flags)
            outcome.errors[:0] = pre_errors
        _exact_evidence(record, outcome.spans)
        record["evidence"] = outcome.spans
        record["check"] = outcome.result()

        status, questions = self.status(
            record, outcome, form_failed=submitted is not None
        )
        record["capture"]["status"] = status
        record["capture"]["questions"] = questions
        return Draft(record, outcome, status, questions)

    @staticmethod
    def status(
        record: dict, outcome: CheckOutcome, form_failed: bool
    ) -> tuple[str, list[str]]:
        """Code sets the status from the content (D25); the model never writes it."""
        if form_failed:
            return "check_failed", []
        capture = record["capture"]
        if capture["reason_code"] and not record["nodes"]:
            return "not_captured", []
        questions = list(outcome.questions)
        for a in record["ambiguities"]:
            if a.get("ask_user"):
                questions.append(
                    a.get("question")
                    or f"By {a['phrase']!r}, do you mean {' or '.join(a['options'])}?"
                )
        for u in record["unresolved"]:
            questions.append(
                f"I could not match {u['phrase']!r}: {u['reason']} "
                "Can you say it another way?"
            )
        if questions:
            if outcome.errors:
                return "check_failed", questions
            return "needs_clarification", questions
        if outcome.errors:
            return "check_failed", []
        return "captured", []

    # ── storing ──────────────────────────────────────────────────────────────

    def finish(self, draft: Draft) -> dict:
        """Store the record; derive and store the resolved record when captured."""
        record = draft.record
        self.store.write("request", record)
        resolved = None
        if draft.status == "captured":
            n = sum(
                1
                for kind, _ in self.store.pointing_to(record["request_id"])
                if kind == "resolved"
            )
            resolved_id = f"res-{record['request_id'].removeprefix('req-')}-{n + 1:03d}"
            resolved = derive(record, self.ctx, resolved_id, now())
            self.store.write("resolved", resolved)
        return reply(record, resolved)

    def submit(self, sub: Submission) -> dict:
        """The client-model path: every submission is stored (D28)."""
        sub, follows, earlier_text = self.resolve_links(sub)
        attempts = 1
        if sub.corrects_request_id:
            failed = self.store.read("request", sub.corrects_request_id)
            attempts = self.chain_depth(failed) + 2
        draft = self.prepare(sub, follows, earlier_text, attempts=attempts)
        return self.finish(draft)


def _error(part: str, message: str) -> dict:
    return {
        "part": part,
        "node": None,
        "field": None,
        "message": message,
        "suggestions": [],
    }


def _exact_evidence(record: dict, spans: list[dict]) -> None:
    """Replace each quoted phrase with the exact span found for it (D24)."""
    found = {s["where"]: s["text"] for s in spans}
    for node in record["nodes"]:
        nid = node.get("id")
        period = node.get("period") or {}
        if f"{nid}.period.text" in found:
            period["text"] = found[f"{nid}.period.text"]
        for name, choice in (node.get("stated") or {}).items():
            if choice and f"{nid}.stated.{name}" in found:
                choice["evidence"] = found[f"{nid}.stated.{name}"]
    for key, field_ in (
        ("answers", "asks"),
        ("ambiguities", "phrase"),
        ("unresolved", "phrase"),
    ):
        for i, item in enumerate(record[key]):
            where = f"{key}.{i}.{field_}"
            if where in found:
                item[field_] = found[where]


NEXT_STEP = {
    "captured": "The request is captured and resolved. Call run_request with this "
    "request_id to compute it (evaluate_request shows the verdicts without "
    "computing), and show its reply text to the user as written.",
    "needs_clarification": "Ask the user the questions as written, then submit their "
    "answer as a new record with answers_request_id set to this request_id.",
    "check_failed": "Correct the record using the errors and submit it again with "
    "corrects_request_id set to this request_id. Take names only from lookup answers.",
    "not_captured": "Tell the user, in plain words, why the request cannot be answered "
    "from the CE Interview Survey.",
}


# On the server-side path the client holds no record to correct.
SERVER_CHECK_FAILED = (
    "The server's model could not write a record that passes the check. Call "
    "capture_request again with the user's message, or ask the user to put the "
    "request another way."
)


def reply(record: dict, resolved: dict | None) -> dict:
    """What a capture tool returns: enough to act on, without the whole record."""
    capture = record["capture"]
    next_step = NEXT_STEP[capture["status"]]
    if capture["status"] == "check_failed" and capture["path"] == "server_model":
        next_step = SERVER_CHECK_FAILED
    out: dict = {
        "request_id": record["request_id"],
        "status": capture["status"],
        "next": next_step,
    }
    if capture["questions"]:
        out["questions"] = capture["questions"]
    if record["check"]["errors"]:
        out["errors"] = record["check"]["errors"]
    if record["check"]["flags"]:
        out["flags"] = record["check"]["flags"]
    if record["fixes"]:
        out["fixes"] = record["fixes"]
    if capture["reason_code"]:
        out["reason_code"] = capture["reason_code"]
    if resolved is not None:
        out["resolved_id"] = resolved["resolved_id"]
        out["resolved"] = {
            "nodes": [_summary(n) for n in resolved["nodes"]],
            "flags": resolved["flags"],
        }
    return out


def _summary(node: dict) -> dict:
    """One resolved node in short: each choice and where it came from."""
    out = {"id": node["id"], "kind": node["kind"]}
    for key, value in node.items():
        if key in ("id", "kind"):
            continue
        if key == "year_sets":
            out["years"] = [y["survey_year"] for y in value]
        elif isinstance(value, dict) and "source" in value:
            shown = value.get("value", value.get("result"))
            if isinstance(shown, (dict, list)):
                out[key] = {"value": shown, "source": value["source"]}
            else:
                out[key] = f"{shown} ({value['source']})"
        elif key in ("inputs", "statistic"):
            out[key] = value
    return out
