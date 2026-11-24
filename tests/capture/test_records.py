"""The record models and their schema files (`ce_capture.records`)."""

import json

import pytest
from pydantic import ValidationError

from ce_capture.records import (
    SCHEMA_DIR,
    SCHEMAS,
    ModelOutput,
    RequestRecord,
    ResolvedRecord,
    dump,
    schema_text,
)
from tests.capture.conftest import ROOT, output

SAMPLES = ROOT.parent


def test_the_schema_files_are_current():
    for name, model in SCHEMAS.items():
        assert (SCHEMA_DIR / name).read_text() == schema_text(model), (
            f"{name} is out of date: run `uv run ce-capture schemas`"
        )


def test_the_model_output_schema_has_no_code_fields():
    props = ModelOutput.model_json_schema()["properties"]
    assert set(props) == {
        "nodes",
        "patch",
        "answers",
        "ambiguities",
        "unresolved",
        "reason_code",
        "reason",
    }


@pytest.mark.skipif(
    not (SAMPLES / "sample-request-record.json").exists(), reason="no sample files"
)
def test_the_hand_written_sample_validates():
    sample = json.loads((SAMPLES / "sample-request-record.json").read_text())
    part = {k: sample[k] for k in ("nodes", "answers", "ambiguities", "unresolved")}
    assert len(ModelOutput.model_validate(part).nodes) == 4
    resolved = json.loads((SAMPLES / "sample-resolved-record.json").read_text())
    ResolvedRecord.model_validate(resolved)


def test_a_wrong_value_is_refused():
    bad = output()
    bad["nodes"][0]["statistic"] = "median"
    with pytest.raises(ValidationError, match="statistic"):
        ModelOutput.model_validate(bad)
    bad = output()
    bad["nodes"][0]["stated"]["weighted"]["value"] = "maybe"
    with pytest.raises(ValidationError):
        ModelOutput.model_validate(bad)
    bad = output()
    bad["nodes"][0]["colour"] = "red"  # no extra fields
    with pytest.raises(ValidationError, match="colour"):
        ModelOutput.model_validate(bad)


def test_dump_leaves_unstated_choices_out():
    data = dump(ModelOutput.model_validate(output()))
    assert set(data["nodes"][0]["stated"]) == {"gap_2025", "weighted"}
    assert data["nodes"][0]["measure"] == {"table": "fmli", "column": "totexpcq"}


def test_a_stored_record_validates(pipeline):
    from tests.capture.conftest import submission

    reply = pipeline.submit(submission())
    record = pipeline.store.read("request", reply["request_id"])
    RequestRecord.model_validate(record)
    resolved = pipeline.store.read("resolved", reply["resolved_id"])
    ResolvedRecord.model_validate(resolved)
