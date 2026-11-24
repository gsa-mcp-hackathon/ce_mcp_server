"""Signatures: when two records compute the same thing."""

from ce_capture.derive import derive
from ce_eval.signature import (
    answered,
    digest,
    equivalence,
    filter_form,
    outcome_score,
    signatures,
)
from tests.eval.conftest import AS_RELEASED, PERIOD

RENT = {"table": "mtbi", "column": "ucc", "comparison": "eq", "value": "210110"}


def _pair(ctx, *nodes, answers=None, status="captured"):
    request = {
        "request_id": "r",
        "sidecars": {},
        "nodes": list(nodes),
        "answers": answers or [{"asks": "x", "node": nodes[-1]["id"]}],
        "capture": {"status": status},
    }
    return request, derive(request, ctx, "res", "t")


def _mean(column="totexpcq", table="fmli", **more):
    node = {
        "id": "n1",
        "kind": "estimate",
        "statistic": "mean",
        "measure": {"table": table, "column": column},
        "group_by": [],
        "filters": [],
        "period": PERIOD,
        "stated": dict(AS_RELEASED),
    }
    node.update(more)
    return node


def test_either_column_of_a_pair_is_the_same_measure(real_ctx):
    a = answered(*_pair(real_ctx, _mean("totexpcq")))
    b = answered(*_pair(real_ctx, _mean("totexppq")))
    assert a == b
    assert a != answered(*_pair(real_ctx, _mean("fdawaycq")))


def test_a_named_version_resolved_to_the_averaged_column_is_that_column(real_ctx):
    assert answered(*_pair(real_ctx, _mean("fincbtx1"))) == answered(
        *_pair(real_ctx, _mean("fincbtxm"))
    )


def test_equal_filters_in_different_forms_are_equal(real_ctx):
    assert filter_form(RENT) == filter_form(
        RENT | {"comparison": "in", "value": ["210110"]}
    )
    a = _mean("cost", "mtbi", filters=[RENT])
    b = _mean(
        "cost", "mtbi", filters=[RENT | {"comparison": "in", "value": ["210110"]}]
    )
    assert answered(*_pair(real_ctx, a)) == answered(*_pair(real_ctx, b))
    c = _mean("cost", "mtbi", filters=[RENT | {"value": "470111"}])
    assert answered(*_pair(real_ctx, a)) != answered(*_pair(real_ctx, c))


def test_what_changes_the_figures_changes_the_signature(real_ctx):
    base = answered(*_pair(real_ctx, _mean()))
    changed = [
        _mean(group_by=[{"table": "fmli", "column": "region"}]),
        _mean(stated={**AS_RELEASED, "weighted": {"value": False, "evidence": "x"}}),
        _mean(
            stated={**AS_RELEASED, "time_basis": {"value": "quarter", "evidence": "x"}}
        ),
        _mean(statistic="total"),
        _mean(period={"text": "2024", "kind": "year", "values": [2024]}, stated={}),
    ]
    for node in changed:
        assert answered(*_pair(real_ctx, node)) != base
    # a choice stated with the default's value changes nothing computed
    stated = _mean(
        stated={**AS_RELEASED, "time_basis": {"value": "year", "evidence": "x"}}
    )
    assert answered(*_pair(real_ctx, stated)) == base


def test_node_names_and_order_do_not_matter(real_ctx):
    ratio = {
        "id": "n3",
        "kind": "combine",
        "statistic": "ratio",
        "inputs": {"numerator": "n1", "denominator": "n2"},
    }
    one = _pair(real_ctx, _mean(), _mean("fincbtxm", id="n2"), ratio)
    other = _pair(
        real_ctx,
        _mean("fincbtxm", id="b"),
        _mean(id="a"),
        {
            "id": "c",
            "kind": "combine",
            "statistic": "ratio",
            "inputs": {"numerator": "a", "denominator": "b"},
        },
    )
    assert answered(*one) == answered(*other)
    swapped = _pair(
        real_ctx,
        _mean(),
        _mean("fincbtxm", id="n2"),
        ratio | {"inputs": {"numerator": "n2", "denominator": "n1"}},
    )
    assert answered(*one) != answered(*swapped)
    assert len(signatures(*one)) == 3 and len(digest(None)) == 12


def test_equivalence_is_the_share_of_statistics_in_common():
    assert equivalence(["a", "b"], ["b", "a"]) == 1.0
    assert equivalence(["a"], ["a", "b"]) == 0.5
    assert equivalence(["a", "a"], ["a"]) == 0.5
    assert equivalence([], []) == 1.0 and equivalence(["x"], ["y"]) == 0.0


def test_outcome_score_by_status(real_ctx):
    got = _pair(real_ctx, _mean("totexppq"))
    gold = _pair(real_ctx, _mean("totexpcq"))
    assert outcome_score(*got, *gold) == 1.0
    asked = ({"capture": {"status": "needs_clarification"}, "nodes": []}, None)
    assert outcome_score(*asked, *gold) == 0.0
    assert outcome_score(*asked, *asked) == 1.0
    assert outcome_score(*got, *asked) == 0.0
