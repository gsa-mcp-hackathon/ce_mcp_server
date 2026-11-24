"""Applying a follow-up patch to the earlier record (D12).

For "now do the same by state" a model writes changes, not the nodes again.
Code copies the earlier record's nodes, applies the changes in order, and
records what was carried over, so the stored follow-up stands alone.
"""

from __future__ import annotations

import copy

from ce_capture.records import dump

ALL = "*"


class PatchError(ValueError):
    """A change that names a node the earlier record does not have."""


def _targets(nodes: list[dict], name: str, op: str) -> list[dict]:
    if name == ALL:
        found = [n for n in nodes if n.get("kind") == "estimate"]
    else:
        found = [n for n in nodes if n.get("id") == name]
    if not found:
        known = ", ".join(n.get("id", "?") for n in nodes) or "none"
        raise PatchError(f"{op} names node {name!r}; the nodes are: {known}.")
    return found


def apply_patch(earlier: dict, patch: list) -> tuple[list[dict], list[dict], dict]:
    """The follow-up's nodes and answers, and what was carried over.

    `earlier` is the stored earlier record; `patch` is the model's list of
    changes (validated `Change` models). Earlier answers whose node survives
    are kept, so every node still says which words it answers.
    """
    nodes = copy.deepcopy(earlier.get("nodes") or [])
    before = {n["id"]: copy.deepcopy(n) for n in nodes}
    added: list[str] = []
    removed: list[str] = []
    for change in patch:
        c = dump(change)
        op = c["op"]
        if op == "add_node":
            node = c["add"]
            if any(n.get("id") == node.get("id") for n in nodes):
                raise PatchError(f"add_node: a node {node.get('id')!r} exists already.")
            nodes.append(node)
            added.append(node["id"])
            continue
        if op == "remove_node":
            for node in _targets(nodes, c["node"], op):
                nodes.remove(node)
                removed.append(node["id"])
            continue
        for node in _targets(nodes, c["node"], op):
            if op == "set_group_by":
                node["group_by"] = c["group_by"]
            elif op == "add_filter":
                node.setdefault("filters", []).append(c["filter"])
            elif op == "remove_filter":
                node["filters"] = [
                    f
                    for f in node.get("filters") or []
                    if (f.get("table"), f.get("column")) != (c["table"], c["column"])
                ]
            elif op == "set_period":
                node["period"] = c["period"]
            elif op == "set_statistic":
                node["statistic"] = c["statistic"]
            elif op == "set_measure":
                node["measure"] = c["measure"]
            elif op == "set_stated":
                node.setdefault("stated", {})[c["field"]] = {
                    "value": c["value"],
                    "evidence": c["evidence"],
                }
            elif op == "remove_stated":
                (node.get("stated") or {}).pop(c["field"], None)

    kept = {n["id"] for n in nodes}
    carried = {
        "from_request_id": earlier["request_id"],
        "unchanged": [i for i, n in before.items() if i in kept and _same(n, nodes, i)],
        "changed": [i for i in before if i in kept and not _same(before[i], nodes, i)],
        "added": added,
        "removed": removed,
    }
    answers = [a for a in earlier.get("answers") or [] if a.get("node") in kept]
    return nodes, answers, carried


def _same(node: dict, nodes: list[dict], nid: str) -> bool:
    return next((n for n in nodes if n.get("id") == nid), None) == node
