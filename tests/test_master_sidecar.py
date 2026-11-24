"""Cross-checks on sidecars/ce-interview-master.odcs.yaml (Sidecar Plan step 5).

These are early versions of the set validator's checks (build step 8): the
master's ce: properties against the vocabulary, its table list and
relationships against each other, and its pointers against the files they
name.
"""

import re
from pathlib import Path

import pytest
import yaml

from ce_meta.sidecars import custom_properties

SIDECARS = Path(__file__).parents[1] / "sidecars"


def _load(name: str) -> dict:
    return yaml.safe_load((SIDECARS / name).read_text())


@pytest.fixture(scope="module")
def master() -> dict:
    return _load("ce-interview-master.odcs.yaml")


@pytest.fixture(scope="module")
def vocab() -> dict:
    return _load("vocabulary.yaml")


@pytest.fixture(scope="module")
def props(master) -> dict:
    return custom_properties(master)


def _allowed(vocab: dict, prop: str, field: str) -> set[str]:
    """Allowed values of one field inside a vocabulary entry's values text,
    e.g. cardinality inside ce:relationships."""
    text = str(vocab["properties"][prop]["values"])
    m = re.search(rf"{field}: \[([^\]]+)\]", text)
    assert m, f"{prop}.{field} not in vocabulary"
    return {v.strip() for v in m.group(1).split(",")}


def test_identity(master):
    assert (master["apiVersion"], master["kind"]) == ("v3.2.0", "DataContract")
    assert master["id"] == "ce-interview-master"
    assert "schema" not in master  # the master describes no columns


def test_every_property_is_a_survey_property(vocab, props):
    for name in props:
        assert name in vocab["properties"], name
        assert vocab["properties"][name]["level"] == "survey", name


def test_vocabulary_pointer_matches(vocab, props):
    assert props["ce:vocabulary"] == {"id": vocab["id"], "version": vocab["version"]}


def test_design_and_geography_values(vocab, props):
    design_types = vocab["properties"]["ce:design"]["values"]["type"]
    assert props["ce:design"]["type"] in design_types
    levels = _allowed(vocab, "ce:geographyLevels", "level")
    assert set(props["ce:geographyLevels"].values()) <= levels
    kinds = _allowed(vocab, "ce:breaks", "kind")
    assert {b["kind"] for b in props["ce:breaks"]} <= kinds


def test_table_list(vocab, props):
    tables = props["ce:tables"]
    rows = set(vocab["properties"]["ce:rowRepresents"]["values"])
    assert all(t["rowRepresents"] in rows for t in tables)
    weighted = [t["sidecar"] for t in tables if t["carriesWeight"]]
    assert weighted == [props["ce:weight"]["table"]]
    assert props["ce:replicateWeights"]["table"] == weighted[0]


def test_relationships(vocab, props):
    listed = {t["sidecar"] for t in props["ce:tables"]}
    cardinality = _allowed(vocab, "ce:relationships", "cardinality")
    weight_from = _allowed(vocab, "ce:relationships", "weightFrom")
    for r in props["ce:relationships"]:
        assert {r["parent"], r["child"]} <= listed, r
        assert r["cardinality"] in cardinality, r
        assert r["weightFrom"] in weight_from, r
        assert len(r["keys"]["parent"]) == len(r["keys"]["child"]), r
        # same_unit links group rows of one unit; they never route a weight
        assert (r["cardinality"] == "same_unit") == (r["weightFrom"] == "self"), r


def test_every_table_reaches_the_weight(props):
    """Validator check 7: follow only weightFrom parent links."""
    reach = {t["sidecar"] for t in props["ce:tables"] if t["carriesWeight"]}
    routes = [r for r in props["ce:relationships"] if r["weightFrom"] == "parent"]
    changed = True
    while changed:
        changed = False
        for r in routes:
            if r["parent"] in reach and r["child"] not in reach:
                reach.add(r["child"])
                changed = True
    assert reach == {t["sidecar"] for t in props["ce:tables"]}


def test_fmli_sidecar_and_master_point_at_each_other(master, props):
    fmli = _load("ce-interview-fmli.odcs.yaml")
    pointer = custom_properties(fmli)
    assert pointer["ce:master"] == {"id": master["id"], "version": master["version"]}
    entry = next(t for t in props["ce:tables"] if t["sidecar"] == fmli["id"])
    assert entry["version"] == fmli["version"]
    columns = {p["name"] for p in fmli["schema"][0]["properties"]}
    for r in props["ce:relationships"]:
        for side in ("parent", "child"):
            if r[side] == fmli["id"]:
                assert set(r["keys"][side]) <= columns, r
