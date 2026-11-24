"""Loading the record pair (`ce_measures.records`)."""

import copy

import pytest

from ce_measures.errors import NotProcessable, PairMismatch
from ce_measures.records import RecordPair, load_pair, nodes
from tests.measures.conftest import estimate, pair_of, store_pair


def test_a_captured_pair_loads_and_joins_by_id(real_ctx, tmp_path):
    from ce_capture.storage import JsonFileStore

    store = JsonFileStore(tmp_path)
    pair = pair_of(real_ctx, estimate(), estimate(id="n2", statistic="total"))
    rid = store_pair(store, pair)
    loaded = load_pair(store, rid)
    assert loaded.resolved["resolved_id"] == "res-test"
    found = nodes(loaded)
    assert [n.id for n in found] == ["n1", "n2"]
    assert found[1].asked["statistic"] == "total"
    assert found[1].resolved["measure_kind"]["value"] == "calendar_pair"


def test_a_request_not_captured_is_refused_with_its_status(real_ctx, tmp_path):
    from ce_capture.storage import JsonFileStore

    store = JsonFileStore(tmp_path)
    pair = pair_of(real_ctx)
    pair.request["capture"] = {"status": "needs_clarification", "reason": "2025?"}
    store_pair(store, pair)
    with pytest.raises(NotProcessable) as err:
        load_pair(store, "req-test")
    assert (err.value.status, err.value.reason) == ("needs_clarification", "2025?")
    with pytest.raises(NotProcessable, match="No request record"):
        load_pair(store, "req-none")


def test_the_newest_resolved_record_is_chosen_unless_one_is_named(real_ctx, tmp_path):
    from ce_capture.storage import JsonFileStore

    store = JsonFileStore(tmp_path)
    pair = pair_of(real_ctx)
    store_pair(store, pair)
    newer = copy.deepcopy(pair.resolved)
    newer["resolved_id"], newer["derived_at"] = "res-newer", "2026-11-21T00:00:00+00:00"
    store.write("resolved", newer)
    assert load_pair(store, "req-test").resolved["resolved_id"] == "res-newer"
    assert (
        load_pair(store, "req-test", "res-test").resolved["resolved_id"] == "res-test"
    )
    with pytest.raises(NotProcessable):
        load_pair(store, "req-test", "res-other")


def test_differing_node_sets_are_refused(real_ctx):
    pair = pair_of(real_ctx, estimate(), estimate(id="n2"))
    resolved = copy.deepcopy(pair.resolved)
    resolved["nodes"] = resolved["nodes"][:1]
    with pytest.raises(PairMismatch, match="n2"):
        nodes(RecordPair(pair.request, resolved))


def test_combine_nodes_come_after_their_inputs(real_ctx):
    ratio = {
        "id": "n0",
        "kind": "combine",
        "statistic": "ratio",
        "inputs": {"numerator": "n1", "denominator": "n2"},
    }
    pair = pair_of(real_ctx, ratio, estimate(), estimate(id="n2"))
    assert [n.id for n in nodes(pair)] == ["n1", "n2", "n0"]
