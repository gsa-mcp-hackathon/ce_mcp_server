"""How a request may use a column (`ce_meta.usage`)."""

from pathlib import Path

import pytest

from ce_meta.sidecars import load_set
from ce_meta.usage import NO_ROLE, NO_SIDECAR, UNIT_OPEN, review_notes, usable_as

SIDECARS = Path(__file__).parents[2] / "sidecars"


@pytest.fixture
def fmli(meta_sidecar_dir):
    return load_set(meta_sidecar_dir).table("fmli")


@pytest.mark.parametrize(
    ("name", "uses"),
    [
        ("totexpcq", ["measure", "filter"]),
        ("region", ["grouping", "filter"]),
        ("qintrvyr", ["grouping", "filter"]),
        ("newid", []),
        ("finlwt21", []),
        ("fincbt_x", []),
    ],
)
def test_uses_follow_the_role(fmli, name, uses):
    assert usable_as(fmli.column(name)) == uses


def test_an_item_code_groups_and_filters(meta_sidecar_dir):
    ucc = load_set(meta_sidecar_dir).column("mtbi", "ucc")
    assert usable_as(ucc) == ["grouping", "filter"]
    assert review_notes(ucc) == ["ce:codeHierarchy under review", "needs-review"]


def test_a_measure_with_an_open_unit_is_still_a_measure(fmli):
    col = fmli.column("fsalaryx")
    assert usable_as(col) == ["measure", "filter"]
    assert review_notes(col) == [UNIT_OPEN, "needs-review"]


def test_a_column_with_no_role_fills_no_slot_and_says_why(fmli):
    col = fmli.column("mystery")
    assert usable_as(col) == []
    assert review_notes(col) == [NO_ROLE, "needs-review"]


def test_a_settled_column_has_no_notes(fmli):
    assert review_notes(fmli.column("region")) == []


def test_no_sidecar_entry(fmli):
    assert usable_as(None) == []
    assert review_notes(None) == [NO_SIDECAR]


def test_every_column_of_the_real_set_gets_an_answer():
    real = load_set(SIDECARS)
    never = {"identifier", "weight", "replicate_weight", "flag"}
    seen = 0
    for table in real.tables.values():
        for col in table.columns.values():
            uses = usable_as(col)
            notes = review_notes(col)
            role = col.get("ce:role")
            if role in never:
                assert uses == []
            if role is None:
                assert uses == [] and NO_ROLE in notes
            seen += 1
    assert seen > 2000
