"""The shared sidecar reader and the set validator (`ce_meta.sidecars`).

The real set under `sidecars/` is read as it stands. The checks are exercised on
a small set written to a temp folder, broken one way at a time.
"""

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ce_meta.sidecars import (
    MASTER_FILE,
    VOCABULARY_FILE,
    VOCABULARY_SCHEMA_FILE,
    custom_properties,
    load_set,
    validate,
    value_problems,
)

SIDECARS = Path(__file__).parents[2] / "sidecars"
FMLI = "ce-interview-fmli"
MEMI = "ce-interview-memi"


# ── the real set ─────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def real():
    return load_set(SIDECARS)


def test_loads_the_real_set(real):
    assert real.problems == ()
    assert (real.vocabulary.id, real.master.id) == (
        "ce-vocabulary",
        "ce-interview-master",
    )
    assert real.master.facts["ce:vocabulary"]["version"] == real.vocabulary.version
    fmli = real.table("fmli")
    assert fmli is real.table(FMLI)
    assert (fmli.name, fmli.row_key) == ("fmli", ["newid"])
    assert fmli.facts["ce:rowRepresents"] == "consumer_unit_interview"
    assert len(fmli.columns) == 786


def test_column_by_stored_name_or_bls_spelling(real):
    by_stored = real.column("fmli", "fincbtxm")
    assert by_stored is real.column("fmli", "FINCBTXM")
    assert by_stored is real.column(FMLI, "Fincbtxm")
    assert by_stored.business_name == "Income before taxes, imputed"
    assert by_stored.get("ce:unit") == "usd_annual"
    assert by_stored.get("ce:imputed") == {"multiple": True, "iterations": 5}
    # the BLS spelling of a flag differs from its stored name by more than case
    flag = real.column("fmli", "FAM__IZE")
    assert flag is not None and flag.get("ce:role") == "flag"
    assert real.column("fmli", "no_such_column") is None
    assert real.column("no_such_table", "age") is None
    assert real.column("memi", "MEMBNO").get("ce:role") == "identifier"
    assert real.column("expn_rnt", "qyear").get("ce:role") == "period"


def test_codes_tags_and_unverified(real):
    region = real.column("fmli", "region")
    assert ("1", "Northeast") in region.codes
    under_review = [c for c in real.table("fmli").columns.values() if c.needs_review]
    assert len(under_review) == 220
    unit_open = [
        c for c in real.table("fmli").columns.values() if "ce:unit" in c.unverified
    ]
    assert len(unit_open) == 126


def test_facts_come_with_what_the_vocabulary_says_follows(real):
    facts = {
        p: (v, implies) for p, v, implies in real.facts_with_implies("fmli", "region")
    }
    value, implies = facts["ce:geographyLevel"]
    assert value == "region"
    assert implies == "Grouping by this column makes a request of that geography level."
    assert real.facts_with_implies("fmli", "no_such_column") == []


def test_real_set_passes_every_check(real):
    report = validate(real)
    assert report.ok, [f.detail for f in report.failures]
    by_check = {c.check: c.status for c in report.checks}
    assert by_check.pop("odcs_schema") == "skipped"  # the ODCS schema is not here
    assert set(by_check.values()) == {"passed"}
    assert report.usable("fmli") and report.usable("memi")
    assert report.usable("expn_rnt") and not report.set_failures


def test_real_set_holds_every_interview_table(real):
    core = {"fmli", "memi", "mtbi", "itbi", "itii"}
    names = {t.name for t in real.tables.values()}
    expn = {n for n in names if n.startswith("expn_")}
    assert names == core | expn and len(expn) == 43
    assert {t["sidecar"] for t in real.master.tables} == set(real.tables)
    for table in real.tables.values():
        if table.name in expn:
            assert table.row_key == ["newid", "seqno", "alcno"]
            assert table.facts["ce:rowRepresents"] == "expn_record"
    assert real.table("memi").row_key == ["newid", "membno"]
    assert real.column("mtbi", "ucc").get("ce:role") == "item_code"
    # monthly rows say which month they belong to: no calendar pair outside fmli
    paired = {
        t.name
        for t in real.tables.values()
        for c in t.columns.values()
        if c.get("ce:calendarPair")
    }
    assert paired == {"fmli"}


def test_verify_values_are_reported_apart_from_failures(real):
    report = validate(real)
    fmli = [u for u in report.unverified if u.table == FMLI]
    assert len(fmli) == 135
    assert {u.property for u in fmli} == {"ce:unit", "ce:level", "ce:topcoded"}
    assert all(u.column for u in report.unverified)
    # where the item code's hierarchy is defined is still open
    assert ("ce-interview-mtbi", "ucc", "ce:codeHierarchy") in {
        (u.table, u.column, u.property) for u in report.unverified
    }


# ── value rules ──────────────────────────────────────────────────────────────


def test_value_problems_by_kind_of_value_description(real):
    values = {p: e["values"] for p, e in real.vocabulary.properties.items()}
    assert value_problems(values["ce:unit"], "usd_annual") == []
    assert value_problems(values["ce:unit"], "dollars")
    assert value_problems(values["ce:unit"], "VERIFY") == []
    assert value_problems(values["ce:additiveOver"], ["consumer_units"]) == []
    assert value_problems(values["ce:additiveOver"], ["consumer_units", "years"])
    assert value_problems(values["ce:topcoded"], True) == []
    assert (
        value_problems(values["ce:weight"], {"table": FMLI, "column": "finlwt21"}) == []
    )
    assert value_problems(values["ce:weight"], "finlwt21")  # the single-table form
    assert value_problems(values["ce:design"], {"type": "panel"})
    assert value_problems(values["ce:imputed"], {"multiple": True, "versions": 5})
    ok_flags = {"A": "skip_pattern", "W": ["allocated", "imputed", "topcoded"]}
    assert value_problems(values["ce:flagMeanings"], ok_flags) == []
    assert value_problems(values["ce:flagMeanings"], {"W": ["allocated", "bogus"]})
    assert value_problems(values["ce:geographyLevels"], {"state": "limited"}) == []
    assert value_problems(values["ce:geographyLevels"], {"state": "iffy"})
    good = {
        "cardinality": "one_to_many",
        "weightFrom": "parent",
        "childRowsOptional": True,
    }
    assert value_problems(values["ce:relationships"], [good]) == []
    assert value_problems(values["ce:relationships"], [{**good, "cardinality": "many"}])
    assert value_problems(values["ce:sourceName"], "ANYTHING") == []  # free text


# ── a small set, broken one way at a time ────────────────────────────────────


def _cp(**facts) -> list[dict]:
    return [{"property": p.replace("_", ":", 1), "value": v} for p, v in facts.items()]


def _column(name: str, **facts) -> dict:
    return {"name": name, "logicalType": "string", "customProperties": _cp(**facts)}


def _table(table: str, row: str, key: list[str], columns: list[dict]) -> dict:
    return {
        "id": f"ce-interview-{table}",
        "version": "0.1.0",
        "customProperties": _cp(
            ce_master={"id": "ce-interview-master", "version": "0.1.0"}
        ),
        "schema": [
            {
                "name": table,
                "customProperties": _cp(ce_rowRepresents=row, ce_rowKey=key),
                "properties": columns,
            }
        ],
    }


def _small_set() -> dict[str, dict]:
    vocab = yaml.safe_load((SIDECARS / VOCABULARY_FILE).read_text())
    master = {
        "id": "ce-interview-master",
        "version": "0.1.0",
        "customProperties": _cp(
            ce_vocabulary={"id": vocab["id"], "version": vocab["version"]},
            ce_weight={"table": FMLI, "column": "finlwt21"},
            ce_tables=[
                {
                    "sidecar": FMLI,
                    "version": "0.1.0",
                    "rowRepresents": "consumer_unit_interview",
                    "carriesWeight": True,
                },
                {
                    "sidecar": MEMI,
                    "version": "0.1.0",
                    "rowRepresents": "member_interview",
                    "carriesWeight": False,
                },
            ],
            ce_relationships=[
                {
                    "parent": FMLI,
                    "child": MEMI,
                    "keys": {"parent": ["newid"], "child": ["newid"]},
                    "cardinality": "one_to_many",
                    "childRowsOptional": False,
                    "weightFrom": "parent",
                }
            ],
        ),
    }
    fmli = _table(
        "fmli",
        "consumer_unit_interview",
        ["newid"],
        [
            _column("newid", ce_role="identifier", ce_scale="nominal"),
            _column("finlwt21", ce_role="weight", ce_scale="ratio"),
            _column(
                "fincbtax", ce_role="measure", ce_scale="ratio", ce_unit="usd_annual"
            ),
            _column(
                "fincbt_x",
                ce_role="flag",
                ce_flagFor="fincbtax",
                ce_flagMeanings={"D": "valid"},
            ),
        ],
    )
    memi = _table(
        "memi",
        "member_interview",
        ["newid", "membno"],
        [
            _column("newid", ce_role="identifier", ce_scale="nominal"),
            _column("membno", ce_role="identifier", ce_scale="nominal"),
        ],
    )
    return {
        VOCABULARY_FILE: vocab,
        MASTER_FILE: master,
        f"{FMLI}.odcs.yaml": fmli,
        f"{MEMI}.odcs.yaml": memi,
    }


def _write(folder: Path, files: dict[str, dict]) -> Path:
    for name, doc in files.items():
        (folder / name).write_text(yaml.safe_dump(doc, sort_keys=False))
    shutil.copy(SIDECARS / VOCABULARY_SCHEMA_FILE, folder / VOCABULARY_SCHEMA_FILE)
    return folder


def _report(tmp_path: Path, change=None):
    files = copy.deepcopy(_small_set())
    if change:
        change(files)
    return validate(load_set(_write(tmp_path, files)))


def _props(doc: dict) -> list[dict]:
    return doc["customProperties"]


def _columns(files: dict, table: str) -> list[dict]:
    return files[f"{table}.odcs.yaml"]["schema"][0]["properties"]


def test_small_set_passes(tmp_path):
    report = _report(tmp_path)
    assert report.ok, list(report.lines())
    assert report.usable("fmli") and report.usable("memi")


def _vocabulary_version(files):
    files[VOCABULARY_FILE]["version"] = "9.9.9"


def _unknown_master_property(files):
    _props(files[MASTER_FILE]).extend(_cp(ce_madeUp="x"))


def _bad_vocabulary_shape(files):
    files[VOCABULARY_FILE]["properties"]["ce:unit"].pop("implies")


@pytest.mark.parametrize(
    ("change", "check"),
    [
        (_vocabulary_version, "versions_agree"),
        (_unknown_master_property, "properties_defined"),
        (_bad_vocabulary_shape, "vocabulary_schema"),
    ],
)
def test_set_level_failures_block_every_table(tmp_path, change, check):
    report = _report(tmp_path, change)
    assert report.set_failures
    assert {c.check for c in report.checks if c.status == "failed"} == {check}
    assert not report.usable("fmli") and not report.usable("memi")


def _unknown_column_property(files):
    _columns(files, MEMI)[1]["customProperties"].extend(_cp(ce_madeUp="x"))


def _value_outside_list(files):
    _columns(files, MEMI)[1]["customProperties"].extend(_cp(ce_unit="dollars"))


def _wrong_level(files):
    _columns(files, MEMI)[1]["customProperties"].extend(
        _cp(ce_rowRepresents="member_interview")
    )


def _wrong_master_pointer(files):
    _props(files[f"{MEMI}.odcs.yaml"])[0]["value"]["id"] = "some-other-master"


def _older_master_version(files):
    _props(files[f"{MEMI}.odcs.yaml"])[0]["value"]["version"] = "0.0.1"


def _listed_version_differs(files):
    files[f"{MEMI}.odcs.yaml"]["version"] = "0.2.0"


def _key_missing(files):
    _columns(files, MEMI).pop(0)
    files[f"{MEMI}.odcs.yaml"]["schema"][0]["customProperties"][1]["value"] = ["membno"]


def _key_not_identifier(files):
    _columns(files, MEMI)[0]["customProperties"][0]["value"] = "dimension"


def _no_weight_route(files):
    _props(files[MASTER_FILE])[3]["value"][0]["weightFrom"] = "self"
    _props(files[MASTER_FILE])[3]["value"][0]["cardinality"] = "same_unit"


def _row_key_missing_column(files):
    files[f"{MEMI}.odcs.yaml"]["schema"][0]["customProperties"][1]["value"] = [
        "newid",
        "nope",
    ]


@pytest.mark.parametrize(
    ("change", "check"),
    [
        (_unknown_column_property, "properties_defined"),
        (_value_outside_list, "properties_defined"),
        (_wrong_level, "properties_defined"),
        (_wrong_master_pointer, "tables_listed"),
        (_older_master_version, "versions_agree"),
        (_listed_version_differs, "versions_agree"),
        (_key_missing, "relationship_keys"),
        (_key_not_identifier, "relationship_keys"),
        (_no_weight_route, "weight_route"),
        (_row_key_missing_column, "row_key"),
    ],
)
def test_table_level_failures_leave_other_tables_usable(tmp_path, change, check):
    report = _report(tmp_path, change)
    assert report.set_failures == []
    assert {c.check for c in report.checks if c.status == "failed"} == {check}
    assert {f.table for f in report.failures} == {MEMI}
    assert report.usable("fmli") and not report.usable("memi")


def test_dangling_flag(tmp_path):
    def change(files):
        _columns(files, FMLI)[3]["customProperties"][1]["value"] = "nope"

    report = _report(tmp_path, change)
    assert [c.check for c in report.checks if c.status == "failed"] == ["flag_columns"]
    assert not report.usable("fmli") and report.usable("memi")


def test_table_file_missing_or_unlisted(tmp_path):
    files = _small_set()
    files.pop(f"{MEMI}.odcs.yaml")
    files["ce-interview-mtbi.odcs.yaml"] = _table(
        "mtbi",
        "expenditure_item_month",
        ["newid"],
        [_column("newid", ce_role="identifier")],
    )
    report = validate(load_set(_write(tmp_path, files)))
    details = {f.table: f.detail for f in report.failures if f.scope == "table"}
    assert "no sidecar file" in details[MEMI]
    assert "not listed in the master" in details["ce-interview-mtbi"]
    assert report.usable("fmli")


def test_unreadable_files_are_reported_not_raised(tmp_path):
    folder = _write(tmp_path, _small_set())
    (folder / f"{MEMI}.odcs.yaml").write_text("schema: [unclosed")
    report = validate(load_set(folder))
    assert not report.usable("memi") and report.usable("fmli")

    (folder / MASTER_FILE).unlink()
    report = validate(load_set(folder))
    assert report.set_failures and not report.usable("fmli")


def test_verify_is_not_a_failure(tmp_path):
    def change(files):
        _columns(files, FMLI)[2]["customProperties"][2]["value"] = "VERIFY"

    report = _report(tmp_path, change)
    assert report.ok
    assert [(u.table, u.column, u.property) for u in report.unverified] == [
        (FMLI, "fincbtax", "ce:unit")
    ]


def test_custom_properties_helper():
    assert custom_properties({"customProperties": _cp(ce_role="flag")}) == {
        "ce:role": "flag"
    }
    assert custom_properties({}) == {} and custom_properties(None) == {}


def test_reader_needs_only_pyyaml():
    code = (
        "import sys, ce_meta.sidecars; "
        "heavy = {'pyarrow', 'pandas', 'duckdb', 'ce_ingest', 'ce_sidecars'}; "
        "heavy.add('jsonschema'); "
        "print(sorted(heavy & set(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"


def test_validate_command_exit_code_and_output(tmp_path, capsys):
    from ce_sidecars.cli import main

    folder = _write(tmp_path, _small_set())
    assert main(["validate", "--sidecars", str(folder)]) == 0
    assert "passed  tables_listed" in capsys.readouterr().out

    (folder / f"{MEMI}.odcs.yaml").unlink()
    assert main(["validate", "--sidecars", str(folder)]) == 1
    out = capsys.readouterr().out
    assert "failed  tables_listed" in out and f"[table {MEMI}]" in out


def _with_pair(files):
    cols = _columns(files, FMLI)
    cols.append(_column("foodpq", ce_role="measure", ce_calendarPair="foodcq"))
    cols.append(_column("foodcq", ce_role="measure", ce_calendarPair="foodpq"))


def test_calendar_pair_that_names_back_passes(tmp_path):
    assert _report(tmp_path, _with_pair).ok


def test_calendar_pair_must_exist_and_name_back(tmp_path):
    def missing(files):
        _with_pair(files)
        _columns(files, FMLI)[-1]["customProperties"][1]["value"] = "nope"

    report = _report(tmp_path, missing)
    assert [c.check for c in report.checks if c.status == "failed"] == [
        "calendar_pairs"
    ]
    details = " ".join(f.detail for f in report.failures)
    assert "not a column" in details and "does not name it back" in details
    assert not report.usable("fmli") and report.usable("memi")
