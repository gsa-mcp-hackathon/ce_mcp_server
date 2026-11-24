"""sidecars/vocabulary.yaml against its JSON Schema (Sidecar Plan build step 3).

The schema checks the file's shape and value forms. What it can't express,
such as how many properties sit at each level, is checked directly here.
"""

import copy
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

SIDECARS = Path(__file__).parents[1] / "sidecars"


@pytest.fixture(scope="module")
def vocab() -> dict:
    return yaml.safe_load((SIDECARS / "vocabulary.yaml").read_text())


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = json.loads((SIDECARS / "vocabulary.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _errors(validator, doc) -> list[str]:
    return [e.message for e in validator.iter_errors(doc)]


def test_vocabulary_conforms_to_schema(validator, vocab):
    assert _errors(validator, vocab) == []


def test_header(vocab):
    assert (vocab["id"], vocab["version"]) == ("ce-vocabulary", "0.1.3")


def test_level_counts(vocab):
    levels = Counter(e["level"] for e in vocab["properties"].values())
    assert levels == {"survey": 12, "table": 3, "column": 18}


def test_retired_property_is_gone(vocab):
    assert "ce:unitOfAnalysis" not in vocab["properties"]


def test_closed_value_lists(vocab):
    props = vocab["properties"]
    assert "same_unit" in props["ce:relationships"]["values"]
    assert "item_code" in props["ce:role"]["values"]
    assert props["ce:rowRepresents"]["values"][0] == "consumer_unit_interview"
    assert "usd_monthly" in props["ce:unit"]["values"]
    assert "allocated" in props["ce:flagMeanings"]["values"]
    assert "parent_record" in props["ce:flagMeanings"]["values"]
    assert "a list of" in props["ce:flagMeanings"]["values"]


# ── the schema rejects what it should ────────────────────────────────────────


def _broken(vocab, change):
    doc = copy.deepcopy(vocab)
    change(doc)
    return doc


@pytest.mark.parametrize(
    "case, change",
    [
        ("missing field", lambda d: d["properties"]["ce:role"].pop("implies")),
        ("extra field", lambda d: d["properties"]["ce:role"].update(ddi="x")),
        ("unknown level", lambda d: d["properties"]["ce:role"].update(level="row")),
        ("empty meaning", lambda d: d["properties"]["ce:role"].update(meaning=" ")),
        ("bad property name", lambda d: d["properties"].update({"role": {}})),
        (
            "duplicate list value",
            lambda d: d["properties"]["ce:scale"].update(values=["ratio", "ratio"]),
        ),
        (
            "list value with spaces",
            lambda d: d["properties"]["ce:scale"].update(values=["not snake"]),
        ),
        ("bad version", lambda d: d.update(version="0.1")),
        ("missing header field", lambda d: d.pop("id")),
        ("unknown top-level key", lambda d: d.update(owner="x")),
    ],
)
def test_schema_rejects(validator, vocab, case, change):
    assert _errors(validator, _broken(vocab, change)), case
