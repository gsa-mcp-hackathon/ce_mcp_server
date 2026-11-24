"""What a node computes, as a signature, and whether two outcomes are the same.

Two request records can differ and still be right: `totexppq` and `totexpcq`
resolve to the same pair of columns, `eq "210110"` and `in ["210110"]` keep the
same rows. A signature is everything that decides a node's figures, read from
the request and resolved records, with those differences folded away. Since
the computation is deterministic, equal signatures give equal figures, so two
outcomes are compared without reading any data.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

ROLES = {"ratio": ("numerator", "denominator"), "difference": ("first", "second")}
SET_FORM = {"eq": "in", "ne": "not_in"}


def _value(entry: Any) -> Any:
    if isinstance(entry, dict) and "source" in entry:
        return entry.get("value", entry.get("result"))
    return entry


def _text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def filter_form(flt: dict) -> list:
    """A filter with equal forms made one: `eq x` is `in [x]`, values sorted."""
    comparison = flt.get("comparison")
    value = flt.get("value")
    values = value if isinstance(value, list) else [value]
    if comparison in SET_FORM:
        comparison = SET_FORM[comparison]
    shown = [_text(v) for v in values]
    if comparison in ("in", "not_in"):
        shown = sorted(set(shown))
    return [flt.get("table"), flt.get("column"), comparison, shown]


def _columns(resolved: dict, asked: dict) -> list[str]:
    value = _value(resolved.get("columns"))
    if isinstance(value, dict):
        return sorted({c for cols in value.values() for c in cols})
    if isinstance(value, list):
        return sorted(value)
    measure = asked.get("measure") or {}
    return [measure["column"]] if measure.get("column") else []


def node_signature(asked: dict, resolved: dict, done: dict[str, dict]) -> dict:
    """The signature of one node. `done` holds the signatures of the nodes
    before it, for a combine node's inputs."""
    statistic = asked.get("statistic")
    if asked.get("kind") == "combine":
        inputs = asked.get("inputs") or {}
        return {
            "statistic": statistic,
            "inputs": [done.get(inputs.get(r)) for r in ROLES.get(statistic, ())],
            "conversion": _value(resolved.get("conversion")),
        }
    measure = asked.get("measure") or None
    sig: dict[str, Any] = {
        "statistic": statistic,
        "table": measure.get("table") if measure else None,
        "columns": _columns(resolved, asked) if measure else [],
        "group_by": sorted(
            [r.get("table"), r.get("column")] for r in asked.get("group_by") or []
        ),
        "filters": sorted(filter_form(f) for f in asked.get("filters") or []),
        "years": sorted(s.get("survey_year") for s in resolved.get("year_sets") or []),
        "within_year": _value(resolved.get("within_year")),
        "weight": _value(resolved.get("weight")),
        "standard_error": bool(_value(resolved.get("standard_error"))),
        "gap_2025": _value(resolved.get("gap_2025")),
    }
    if statistic in ("mean", "total"):
        sig["measure_kind"] = _value(resolved.get("measure_kind"))
        sig["time_basis"] = _value(resolved.get("time_basis"))
        sig["missing_codes"] = _value(resolved.get("missing_codes"))
    else:
        sig["count_method"] = _value(resolved.get("count_method"))
        sig["count_unit"] = _value(resolved.get("count_unit"))
    return sig


def digest(signature: dict | None) -> str:
    text = json.dumps(signature, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def signatures(request: dict, resolved: dict | None) -> dict[str, dict]:
    """Every node's signature, by node id. Combine nodes come after their inputs."""
    asked = {n["id"]: n for n in request.get("nodes") or []}
    res = {n["id"]: n for n in (resolved or {}).get("nodes") or []}
    done: dict[str, dict] = {}
    pending = list(asked)
    while pending:
        ready = [
            i
            for i in pending
            if asked[i].get("kind") != "combine"
            or all(v in done for v in (asked[i].get("inputs") or {}).values() if v)
        ]
        if not ready:
            ready = pending[:1]
        for i in ready:
            done[i] = node_signature(asked[i], res.get(i, {}), done)
            pending.remove(i)
    return done


def answered(request: dict, resolved: dict | None) -> list[str]:
    """The digests of the nodes that answer the user's phrases, sorted: what
    the user will be shown, whatever the nodes are called."""
    sigs = signatures(request, resolved)
    nodes = [a.get("node") for a in request.get("answers") or []]
    if not nodes:  # a follow-up with no new phrase answers with every node
        nodes = list(sigs)
    return sorted(digest(sigs.get(n)) for n in dict.fromkeys(nodes))


def equivalence(got: list[str], gold: list[str]) -> float:
    """The share of answered statistics two outcomes have in common: 1.0 when
    they compute the same things, counted once each."""
    if not gold and not got:
        return 1.0
    left, right = list(got), list(gold)
    matched = 0
    for d in list(left):
        if d in right:
            right.remove(d)
            matched += 1
    return matched / max(len(got), len(gold))


def outcome_score(
    got: dict, got_resolved: dict | None, gold: dict, gold_resolved: dict | None
) -> float:
    """The outcome level for two request records with their resolved records:
    the share of answered statistics in common when the gold is captured, and
    otherwise whether the status is the same."""
    status = (got.get("capture") or {}).get("status")
    gold_status = (gold.get("capture") or {}).get("status")
    if gold_status != "captured":
        return 1.0 if status == gold_status else 0.0
    if status != "captured":
        return 0.0
    return equivalence(answered(got, got_resolved), answered(gold, gold_resolved))
