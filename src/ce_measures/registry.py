"""Statistic term → measure module, and the method a resolved node names.

The method is named by the resolved record's `measure_kind`, `count_method`
and `weight` (P8): validation does not re-derive them.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

from ce_measures.errors import UnknownMethod
from ce_measures.measures import measure_count, measure_mean, measure_total
from ce_measures.models import Node, Rule

MODULES: dict[str, ModuleType] = {
    "mean": measure_mean,
    "total": measure_total,
    "count": measure_count,
}
RULE_TABLES: dict[str, list[Rule]] = {
    "mean": measure_mean.MEAN_RULES,
    "total": measure_total.TOTAL_RULES,
    "count": measure_count.COUNT_RULES,
}


def method_for(node: Node) -> str | None:
    """The `do_*` method of an estimate node, or None when none is defined
    (an unresolved measure kind, or a share)."""
    module = MODULES.get(str(node.asked.get("statistic")))
    if module is None:
        return None
    if node.asked.get("statistic") == "count":
        key = (node.resolved.get("count_method") or {}).get("value")
    else:
        key = (node.resolved.get("measure_kind") or {}).get("value")
    return module.METHODS.get(key)


def rules_for(node: Node) -> list[Rule]:
    from ce_measures.verdict import SE_RULES, SHARED_RULES

    statistic = str(node.asked.get("statistic"))
    own = list(RULE_TABLES.get(statistic, []))
    if node.asked.get("measure"):
        own += SE_RULES
    # Order of the messages: what blocks; then what is said of the column
    # (under review, then the measure's own rules); then grouping and period.
    blocks = [r for r in SHARED_RULES if r.level == "block"]
    review = [r for r in SHARED_RULES if r.id == "unverified_fact"]
    rest = [r for r in SHARED_RULES if r.level != "block" and r.id != "unverified_fact"]
    return blocks + review + own + rest


def function(name: str) -> Callable:
    for module in MODULES.values():
        fn = getattr(module, name, None)
        if name.startswith("do_") and callable(fn):
            return fn
    raise UnknownMethod(f"No measure module has a method {name!r}.")
