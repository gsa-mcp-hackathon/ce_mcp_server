"""The outcome record, the reply text and the two entry points, on the sample
pair captured through Plan 4's pipeline and the real sidecar set, with no
data folder: evaluating opens no table."""

import dataclasses
import json

import jsonschema
import pytest

from ce_capture.storage import JsonFileStore
from ce_measures.errors import NotProcessable
from ce_measures.outcome import (
    RULES_VERSION,
    SCHEMA_FILE,
    dump,
    rules_fingerprint,
    schema_text,
)
from ce_measures.process import RequestProcessor
from tests.measures.conftest import captured, sample_output

# The rule tables' fingerprint at each rules_version. Changing a rule table
# changes the fingerprint: raise RULES_VERSION and add the pair here.
KNOWN_RULES = {"1.0": "42f99f2e287d4ad4", "1.1": "952b35628e82f4cd"}

SAMPLE = sample_output()
needs_sample = pytest.mark.skipif(SAMPLE is None, reason="no sample files")

# The plan's outcome example, judged without computing. One message more than
# the example: n2's top-coded variance (Plan 5, "Standard errors").
EXAMPLE = {
    "n1": (
        "warn",
        "do_calendar_pair_mean",
        [
            ("unverified_fact", "totexpcq", "needs-review"),
            ("region_code_c", "region", None),
            ("gap_2025_as_released", None, None),
        ],
        ["weight finlwt21: defaulted", "missing codes removed: defaulted"],
    ),
    "n2": (
        "warn",
        "do_annual_value_mean",
        [
            ("topcoded_subgroup", "fincbtxm", "ce:topcoded"),
            ("annual_value_reading", None, None),
            ("topcoded_variance", "fincbtxm", "ce:topcoded"),
            ("region_code_c", "region", None),
            ("gap_2025_as_released", None, None),
        ],
        [
            "weight finlwt21: defaulted",
            "missing codes removed: defaulted",
            "five versions averaged: defaulted",
        ],
    ),
    "n3": ("warn", "ratio", [("carried", None, None)], []),
    "n4": (
        "warn",
        "do_distinct_count",
        [("unweighted_count", None, None), ("region_code_c", "region", None)],
        ["weight none: stated"],
    ),
}

# The plan's reply example. One line differs: the example runs the region
# caution to 83 characters, where the text wraps at 80.
REPLY = """\
What's average annual spending per household by region
  Will compute: the mean of total expenditures (totexppq, totexpcq) by region,
  weighted, per consumer unit per year.
  Caution: totexpcq is still under review in the sidecar.
  Caution: region code C ("Combined") is not a region; it is reported
  separately.
  Caution: 2025 as released. Fourth-quarter interviews stand for one third of
  the usual population.

What is that as a share of average annual income before taxes
  Will compute: the ratio of the figure above to the mean of income before
  taxes, imputed (fincbtxm), by region.
  Caution: the cautions on both inputs apply.

How many households did you interview in each region
  Will compute: the number of consumer units interviewed (households are counted
  as consumer units) by region, not weighted.
  Caution: a count of the interviewed sample, not of US households.
  Caution: region code C ("Combined") is not a region; it is reported
  separately.

Interview Survey only; results will not match BLS published tables.
Survey year 2025, as released.
"""


LOADED_2025 = {(2025, "1x"), (2025, "2"), (2025, "3"), (2025, "4"), (2026, "1")}


@pytest.fixture
def processor(real_ctx, tmp_path) -> RequestProcessor:
    """The registry says 2025 is loaded, but there is no data folder at all."""
    ctx = dataclasses.replace(real_ctx, loaded_partitions=set(LOADED_2025))
    return RequestProcessor(ctx, JsonFileStore(tmp_path), data_dir=None)


@pytest.fixture
def unloaded(real_ctx, tmp_path) -> RequestProcessor:
    return RequestProcessor(real_ctx, JsonFileStore(tmp_path), data_dir=None)


def _sample(processor) -> str:
    return captured(processor.ctx, processor.store, SAMPLE["text"], SAMPLE["output"])


def test_the_rules_version_is_raised_with_the_rule_tables():
    assert KNOWN_RULES.get(RULES_VERSION) == rules_fingerprint(), (
        "A rule table changed: raise RULES_VERSION in ce_measures/outcome.py and "
        "record the new fingerprint in KNOWN_RULES."
    )


def test_the_outcome_schema_is_up_to_date():
    assert SCHEMA_FILE.read_text() == schema_text(), "run: uv run ce-measures schema"


@needs_sample
def test_the_sample_pair_gives_the_example_verdicts_without_data(processor):
    record, _ = processor.evaluate(_sample(processor))
    got = {
        n.id: (
            n.verdict,
            n.method,
            [(m.rule, m.column, m.property) for m in n.messages],
            n.notes,
        )
        for n in record.nodes
    }
    assert got == EXAMPLE
    assert record.verdict == "warn" and record.mode == "evaluate"
    assert record.rules_version == RULES_VERSION
    assert record.says == [
        "Interview Survey only; results will not match BLS published tables",
        "Survey year 2025, as released",
    ]
    assert all(n.result is None for n in record.nodes)


@needs_sample
def test_the_sample_pair_gives_the_reply_example(processor):
    record, text = processor.evaluate(_sample(processor))
    assert text == REPLY and record.reply == REPLY


@needs_sample
def test_the_record_is_stored_validates_and_each_run_writes_a_new_one(processor):
    rid = _sample(processor)
    first, _ = processor.evaluate(rid)
    second, _ = processor.evaluate(rid)
    assert first.outcome_id != second.outcome_id
    stored = processor.store.read("outcome", first.outcome_id)
    assert stored == dump(first)
    jsonschema.validate(stored, json.loads(SCHEMA_FILE.read_text()))
    linked = [
        r["outcome_id"] for k, r in processor.store.pointing_to(rid) if k == "outcome"
    ]
    assert sorted(linked) == sorted([first.outcome_id, second.outcome_id])


@needs_sample
def test_a_period_not_loaded_blocks_every_node(unloaded):
    record, text = unloaded.evaluate(_sample(unloaded))
    assert record.verdict == "block"
    assert {n.verdict for n in record.nodes} == {"block"}
    assert "No data is loaded for that period" in text


@needs_sample
def test_a_run_with_no_stored_rows_blocks_with_the_reason(processor):
    record, text = processor.run(_sample(processor))
    assert record.verdict == "block" and record.mode == "run"
    n1 = next(n for n in record.nodes if n.id == "n1")
    assert n1.messages[-1].rule == "not_computed"
    assert "No data folder is configured" in text


def test_a_request_needing_clarification_is_not_processed(processor):
    output = {
        "nodes": [
            {
                "id": "n1",
                "kind": "estimate",
                "statistic": "mean",
                "measure": {"table": "fmli", "column": "totexpcq"},
                "group_by": [],
                "filters": [],
                "period": {"text": "in 2025", "kind": "year", "values": [2025]},
                "stated": {},
            }
        ],
        "answers": [{"asks": "average spending", "node": "n1"}],
        "ambiguities": [],
        "unresolved": [],
    }
    from ce_capture.pipeline import CapturePipeline, Submission

    reply = CapturePipeline(processor.ctx, processor.store).submit(
        Submission("What was average spending in 2025?", "s1", output)
    )
    assert reply["status"] == "needs_clarification"
    with pytest.raises(NotProcessable) as err:
        processor.evaluate(reply["request_id"])
    assert err.value.status == "needs_clarification"
    assert not [
        k for k, _ in processor.store.pointing_to(reply["request_id"]) if k == "outcome"
    ]
