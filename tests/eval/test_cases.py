"""The case sets: their form, and that they cover what Plan 6 asks for."""

from collections import Counter

import pytest
import yaml

from ce_eval.cases import SETS, CaseError, load_cases
from tests.eval.conftest import CASES


def test_the_five_sets_load_with_about_the_planned_counts(cases):
    counts = Counter(c.set for c in cases.cases)
    assert set(counts) == set(SETS)
    assert counts["core"] == 16
    assert counts["traps"] >= 45
    assert counts["rewordings"] == 15
    assert counts["conversations"] >= 10
    assert counts["out_of_scope"] >= 8
    assert cases.loaded_years == (2025,)


def test_every_case_names_a_known_nuance_and_every_nuance_has_a_case(cases):
    assert all(c.nuance for c in cases.cases)
    used = {n for c in cases.cases for n in c.nuance}
    assert used == set(cases.nuances), sorted(set(cases.nuances) ^ used)


def test_every_trap_says_what_mistake_it_invites(cases):
    traps = [c for c in cases.cases if c.set == "traps"]
    assert all(c.mistake for c in traps)


def test_rewordings_are_three_wordings_of_five_core_cases(cases):
    by_base = Counter(c.rewords for c in cases.cases if c.set == "rewordings")
    assert len(by_base) == 5 and set(by_base.values()) == {3}
    core = {c.id for c in cases.cases if c.set == "core"}
    assert set(by_base) <= core


def test_conversations_keep_their_turns_together_and_in_order(cases):
    chains = {conv[0].id: [c.id for c in conv] for conv in cases.conversations()}
    assert chains["conv-spending"] == [
        "conv-spending",
        "conv-spending-by-region",
        "conv-spending-renters",
    ]
    assert chains["conv-health"] == ["conv-health", "conv-renters-count"]
    assert "conv-people-means-households" in chains["people-ambiguous"]
    by_id = cases.by_id()
    assert by_id["conv-renters-count"].after == "conv-health"
    assert by_id["conv-renters-count"].follows is None  # a new request, no link


def test_a_selection_keeps_whole_conversations(cases):
    picked = cases.select(ids=["conv-spending-renters"])
    assert [c.id for c in picked.cases] == [
        "conv-spending",
        "conv-spending-by-region",
        "conv-spending-renters",
    ]
    assert {c.set for c in load_cases(CASES, ["traps"]).cases} == {"traps"}


def test_a_malformed_file_is_refused(tmp_path):
    def write(cases_list):
        doc = {"set": "core", "cases": cases_list}
        (tmp_path / "core.yaml").write_text(yaml.safe_dump(doc))

    write([{"id": "a", "message": "m", "gold": {}}])
    with pytest.raises(CaseError, match="no expect"):
        load_cases(tmp_path)
    case = {"id": "a", "message": "m", "gold": {}, "expect": {}}
    write([case, dict(case)])
    with pytest.raises(CaseError, match="twice"):
        load_cases(tmp_path)
    write([case | {"follows": "nothing"}])
    with pytest.raises(CaseError, match="not a case"):
        load_cases(tmp_path)
