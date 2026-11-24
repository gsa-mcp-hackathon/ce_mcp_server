import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ce_ingest.derive import add_partition_columns
from ce_ingest.profile import (
    PROFILE_SCHEMA,
    PROFILE_TABLE,
    UNMATCHED_SCHEMA,
    UNMATCHED_TABLE,
    finish_profile,
    mark_undocumented,
    profile_table,
    same_code,
    unmatched_codes,
)
from ce_ingest.writers import ParquetWriter


def _store(writer, make_src, table, parts):
    for rel, t in parts:
        src = make_src(f"intrvw25/{table}{rel}")
        writer.write_partition(src, add_partition_columns(t, src))


def _data(n_offset=0):
    return pa.table(
        {
            "newid": pa.array([f"0566606{i}" for i in range(4)]),
            "cuid": pa.array(["0566606"] * 4),
            "state": pa.array(["01", "06", "06", None]),
            "cost": pa.array([1.5, None, 3.0, 10.0 + n_offset]),
        }
    )


@pytest.fixture
def stored(out_dir, make_src):
    w = ParquetWriter(out_dir)
    _store(w, make_src, "fmli", [("251x", _data()), ("252", _data(1))])  # wide
    _store(w, make_src, "mtbi", [("251x", _data()), ("252", _data(1))])  # item
    return w


def _by_col(t: pa.Table) -> dict[str, dict]:
    return {r["column_name"]: r for r in t.to_pylist()}


def test_profile_stats(stored):
    prof = _by_col(profile_table(stored, "intrvw", "mtbi"))
    state, cost = prof["state"], prof["cost"]
    assert (state["row_count"], state["null_count"]) == (8, 2)
    assert state["null_share"] == pytest.approx(0.25)
    assert state["distinct_count"] == 2
    assert state["top_values"] == [
        {"value": "06", "count": 4},
        {"value": "01", "count": 2},
    ]
    assert (cost["min"], cost["max"], cost["distinct_count"]) == ("1.5", "11.0", 4)
    assert prof["quarter_code"]["distinct_count"] == 2
    assert prof["survey_year"]["data_type"] == "INTEGER"


def test_no_top_values_for_id_columns(stored):
    prof = _by_col(profile_table(stored, "intrvw", "fmli"))
    assert prof["newid"]["top_values"] is None
    assert prof["cuid"]["top_values"] is None
    assert prof["newid"]["distinct_count"] == 4  # stats are still kept
    assert prof["state"]["top_values"]


def test_wide_and_item_give_the_same_profile(stored):
    wide = profile_table(stored, "intrvw", "fmli")
    item = profile_table(stored, "intrvw", "mtbi")
    assert wide.schema == item.schema == PROFILE_SCHEMA
    drop = ["dataset_id", "table", "profiled_at"]
    assert wide.drop_columns(drop).to_pylist() == item.drop_columns(drop).to_pylist()


def test_reprofile_replaces_rows(stored):
    profile_table(stored, "intrvw", "fmli")
    profile_table(stored, "intrvw", "mtbi")
    profile_table(stored, "intrvw", "fmli")
    rows = pq.read_table(stored.meta_path(PROFILE_TABLE)).to_pylist()
    counts = {}
    for r in rows:
        counts[r["table"]] = counts.get(r["table"], 0) + 1
    assert counts == {"fmli": 7, "mtbi": 7}


def test_tables_lists_stored(stored):
    assert stored.tables() == [("intrvw", "fmli"), ("intrvw", "mtbi")]
    assert stored.tables("diary") == []


def test_missing_table_raises(out_dir):
    with pytest.raises(ValueError, match="no stored partitions"):
        profile_table(ParquetWriter(out_dir), "intrvw", "fmli")


# ── what needs the dictionary tables ─────────────────────────────────────────


def _variables(rows):
    cols = ["variable_name", "table", "first_year", "last_year", "flag_name"]
    return pa.table({c: [r.get(c) for r in rows] for c in cols})


def _codes(rows):
    cols = ["variable_name", "table", "code_value", "first_year", "last_year"]
    return pa.table({c: [r.get(c) for r in rows] for c in cols})


@pytest.mark.parametrize(
    ("value", "known", "expected"),
    [
        ("06", {"01", "06"}, True),
        ("6", {"01", "06"}, True),  # the same number
        ("100.0", {"100"}, True),  # a DOUBLE read as text
        ("201", {"2nn"}, True),  # n stands for any digit
        ("2011", {"2nn"}, False),
        ("C", {"1", "2"}, False),
        ("07", {"01", "06"}, False),
    ],
)
def test_same_code(value, known, expected):
    assert same_code(value, known) is expected


def test_finish_profile_waits_for_the_dictionary_tables(stored):
    profile_table(stored, "intrvw", "fmli")
    assert finish_profile(stored, "intrvw", 2025) is None
    rows = pq.read_table(stored.meta_path(PROFILE_TABLE)).to_pylist()
    assert {r["undocumented"] for r in rows} == {None}


def test_mark_undocumented(stored):
    profile_table(stored, "intrvw", "fmli")
    stored.replace_table(
        "ref",
        "variables",
        _variables(
            [
                {"variable_name": "newid", "table": "fmli", "first_year": 1984},
                # documented through its value column's flag name
                {
                    "variable_name": "zzz",
                    "table": "fmli",
                    "first_year": 1984,
                    "flag_name": "state",
                },
                # ended before the release: not documented for it
                {
                    "variable_name": "cost",
                    "table": "fmli",
                    "first_year": 1984,
                    "last_year": 2010,
                },
                {"variable_name": "cost", "table": "mtbi", "first_year": 1984},
            ]
        ),
    )
    assert mark_undocumented(stored, "intrvw", 2025) == 1
    prof = _by_col(pq.read_table(stored.meta_path(PROFILE_TABLE)))
    assert prof["cost"]["undocumented"] is True
    assert prof["newid"]["undocumented"] is False
    assert prof["state"]["undocumented"] is False
    for loader_column in ("cuid", "survey_year", "quarter", "quarter_code"):
        assert prof[loader_column]["undocumented"] is False


def test_unmatched_codes(stored):
    stored.replace_table(
        "ref",
        "codes",
        _codes(
            [
                {"variable_name": "state", "table": "fmli", "code_value": "01"},
                {"variable_name": "state", "table": "mtbi", "code_value": "01"},
                {"variable_name": "state", "table": "mtbi", "code_value": "06"},
                # a numeric column: the code is compared as a number
                {"variable_name": "cost", "table": "mtbi", "code_value": "3"},
                # a code that ended before the release is not in the list
                {
                    "variable_name": "cost",
                    "table": "mtbi",
                    "code_value": "1.5",
                    "last_year": 2001,
                },
                # ID columns are never scanned
                {"variable_name": "newid", "table": "fmli", "code_value": "x"},
            ]
        ),
    )
    found = unmatched_codes(stored, "intrvw", 2025).to_pylist()
    assert {
        (r["table"], r["column_name"], r["value"], r["row_count"]) for r in found
    } == {
        ("fmli", "state", "06", 4),  # 06 is not in fmli's list; the null is no code
        ("mtbi", "cost", "1.5", 2),
        ("mtbi", "cost", "10.0", 1),
        ("mtbi", "cost", "11.0", 1),
    }
    stored_rows = pq.read_table(stored.meta_path(UNMATCHED_TABLE))
    assert stored_rows.schema == UNMATCHED_SCHEMA
    # a second run replaces the survey's rows
    unmatched_codes(stored, "intrvw", 2025)
    assert pq.read_table(stored.meta_path(UNMATCHED_TABLE)).num_rows == 4


def test_finish_profile_runs_both_steps(stored):
    profile_table(stored, "intrvw", "fmli")
    profile_table(stored, "intrvw", "mtbi")
    stored.replace_table(
        "ref",
        "variables",
        _variables([{"variable_name": "newid", "table": "fmli", "first_year": 1984}]),
    )
    stored.replace_table(
        "ref",
        "codes",
        _codes([{"variable_name": "state", "table": "fmli", "code_value": "01"}]),
    )
    flagged, unmatched = finish_profile(stored, "intrvw", 2025)
    # fmli: state and cost; mtbi: newid, state and cost
    assert (flagged, unmatched) == (5, 1)
