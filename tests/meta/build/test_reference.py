"""The dictionary tables (`ce_meta.build.reference`)."""

import pyarrow.parquet as pq

from ce_ingest.registry import SOURCE_COLUMNS_TABLE
from ce_meta.build.reference import (
    CODES_SCHEMA,
    VARIABLES_SCHEMA,
    build_codes,
    build_diary_variables,
    build_reference,
    build_variables,
    table_name,
)
from ce_sidecars.dictionary import read_dictionary, read_history

STORED = {"fmli", "expn_rnt"}


def test_table_name():
    assert table_name("FMLI", STORED) == "fmli"
    assert table_name("RNT", STORED) == "expn_rnt"
    assert table_name("VEQ", STORED) == "veq"  # no stored table: its own code


def test_read_history_keeps_every_range_of_both_surveys(dictionary):
    history = read_history(dictionary)
    assert len(history.variables) == 8 and len(history.codes) == 18
    renamed = [r for r in history.variables if r.variable.name == "renamed"]
    assert [(r.first_year, r.last_year) for r in renamed] == [
        (1984, 1999),
        (2000, None),
    ]
    assert {r.survey for r in history.variables} == {"INTERVIEW", "DIARY"}
    assert history.variables[0].variable.flag == "regi_n"  # normalized like the loader
    assert read_history(dictionary) is history  # the workbook is read once


def test_read_dictionary_is_a_filter_over_the_history(dictionary):
    current = read_dictionary(dictionary, "fmli", 2025)
    assert set(current.variables) == {"region", "renamed"}
    assert current.variables["renamed"].description == "Second meaning"
    assert [c.value for c in current.codes["region"]] == ["1", "3", "4", "C"]
    assert [
        c.value for c in read_dictionary(dictionary, "fmli", 2024).codes["region"]
    ] == [
        "1",
        "3",
        "4",
    ]
    assert read_dictionary(dictionary, "fmli", 1990).variables[
        "renamed"
    ].description == ("First meaning")


def test_build_variables(dictionary, stored):
    source_columns = stored.read_rows(SOURCE_COLUMNS_TABLE)
    table = build_variables(read_history(dictionary), source_columns, STORED, 2025)
    assert table.schema == VARIABLES_SCHEMA
    rows = table.to_pylist()
    # Interview only, sorted by table, name and first year
    assert [(r["table"], r["variable_name"], r["first_year"]) for r in rows] == [
        ("expn_rnt", "rtcompx", 2013),
        ("fmli", "oldvar", 1984),
        ("fmli", "region", 1984),
        ("fmli", "renamed", 1984),
        ("fmli", "renamed", 2000),
        ("veq", "veqx", 1990),
    ]
    by_key = {(r["table"], r["variable_name"], r["first_year"]): r for r in rows}
    region = by_key[("fmli", "region", 1984)]
    assert (region["source_name"], region["flag_name"]) == ("REGION", "regi_n")
    assert region["data_type"] == "character"  # in the loaded data, and current
    assert by_key[("fmli", "oldvar", 1984)]["data_type"] is None  # ended
    assert by_key[("expn_rnt", "rtcompx", 2013)]["data_type"] is None  # not stored


def test_build_diary_variables_holds_diary_only_names(dictionary):
    names = build_diary_variables(read_history(dictionary))["variable_name"].to_pylist()
    assert names == ["wk_wrkd"]  # `region` is an Interview name too


def test_build_codes(dictionary):
    table = build_codes(read_history(dictionary), STORED)
    assert table.schema == CODES_SCHEMA
    rows = table.to_pylist()
    assert all(r["table"] == "fmli" for r in rows)  # no Diary rows
    region = [r for r in rows if r["variable_name"] == "region"]
    # the exact duplicate is dropped; the two ranges of code 1 are kept
    assert [(r["code_value"], r["first_year"], r["last_year"]) for r in region] == [
        ("1", 1980, 1981),
        ("1", 1996, None),
        ("3", 1996, None),
        ("4", 1996, None),
        ("C", 2025, None),
    ]
    states = [r["code_value"] for r in rows if r["variable_name"] == "state"]
    assert states == ["06", "25", "33", "72"]  # text: the leading zero survives


def test_build_reference_writes_three_tables_and_is_idempotent(dictionary, stored):
    counts = build_reference(stored, dictionary, 2025)
    assert counts == {"variables": 6, "codes": 16, "diary_variables": 1}
    paths = [stored.meta_path(name, "ref") for name in counts]
    first = [p.read_bytes() for p in paths]
    assert pq.read_table(paths[0]).num_rows == 6
    build_reference(stored, dictionary, 2025)
    assert [p.read_bytes() for p in paths] == first
