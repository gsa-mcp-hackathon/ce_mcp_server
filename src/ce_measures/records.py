"""Loading a request record and its resolved record (pipeline step 1).

Records come through Plan 4's storage interface (`ce_capture.storage`), so the
JSON file store and a later Delta store both serve. Only a request with status
captured that has a resolved record is processed.
"""

from __future__ import annotations

from dataclasses import dataclass

from ce_capture.storage import RecordStore
from ce_measures.errors import NotProcessable, PairMismatch
from ce_measures.models import Node


@dataclass(frozen=True)
class RecordPair:
    request: dict
    resolved: dict

    @property
    def flags(self) -> list[str]:
        """Every flag on the pair: the capture check's, then derivation's."""
        check = self.request.get("check") or {}
        return list(check.get("flags") or []) + list(self.resolved.get("flags") or [])


def _resolved_for(store: RecordStore, request_id: str) -> list[dict]:
    """A request's resolved records, newest first."""
    finder = getattr(store, "resolved_for", None)
    if finder is not None:
        return finder(request_id)
    return [r for kind, r in store.pointing_to(request_id) if kind == "resolved"]


def load_pair(
    store: RecordStore, request_id: str, resolved_id: str | None = None
) -> RecordPair:
    """The request record and one of its resolved records: the one named, or the
    newest."""
    request = store.read("request", request_id)
    if request is None:
        raise NotProcessable(f"No request record {request_id}.", None, None)
    capture = request.get("capture") or {}
    status = capture.get("status")
    if status != "captured":
        raise NotProcessable(
            f"Request {request_id} has status {status}; only a captured request "
            "is processed.",
            status,
            capture.get("reason"),
        )
    if resolved_id:
        resolved = store.read("resolved", resolved_id)
        if resolved is None or resolved.get("request_id") != request_id:
            raise NotProcessable(
                f"No resolved record {resolved_id} for request {request_id}.",
                status,
                None,
            )
    else:
        found = _resolved_for(store, request_id)
        if not found:
            raise NotProcessable(
                f"Request {request_id} has no resolved record.", status, None
            )
        resolved = found[0]
    return RecordPair(request, resolved)


def nodes(pair: RecordPair) -> list[Node]:
    """The nodes of both records joined by id, estimate nodes first, then combine
    nodes after the nodes they name, so one pass can process them."""
    asked = {n["id"]: n for n in pair.request.get("nodes") or []}
    resolved = {n["id"]: n for n in pair.resolved.get("nodes") or []}
    if set(asked) != set(resolved):
        only_asked = sorted(set(asked) - set(resolved))
        only_resolved = sorted(set(resolved) - set(asked))
        raise PairMismatch(
            "The request and resolved records list different nodes: "
            f"only in the request {only_asked}, only in the resolved {only_resolved}."
        )
    order = [n["id"] for n in pair.request.get("nodes") or []]
    out = [
        Node(id=i, kind="estimate", asked=asked[i], resolved=resolved[i])
        for i in order
        if asked[i].get("kind") == "estimate"
    ]
    done = {n.id for n in out}
    pending = [i for i in order if asked[i].get("kind") == "combine"]
    while pending:
        ready = [
            i
            for i in pending
            if all(v in done for v in (asked[i].get("inputs") or {}).values() if v)
        ]
        if not ready:  # an input that is no node: the combine rules block it
            ready = pending[:1]
        for i in ready:
            out.append(Node(id=i, kind="combine", asked=asked[i], resolved=resolved[i]))
            done.add(i)
            pending.remove(i)
    return out
