"""Finding tables and columns by name (`ce_meta.names`)."""

import duckdb
import pytest

from ce_meta.catalog import DuckDBCatalog
from ce_meta.errors import OutOfScope, UnknownTable, UnknownVariable
from ce_meta.names import closest_names, ordered_tables, resolve_name, resolve_table
from ce_meta.sidecars import load_set


@pytest.fixture
def sidecars(meta_sidecar_dir):
    return load_set(meta_sidecar_dir)


@pytest.fixture
def catalog(meta_data_dir):
    return DuckDBCatalog(meta_data_dir)


def test_stored_name_and_bls_spelling_resolve_alike(sidecars, catalog):
    expected = [("fmli", "finlwt21")]
    assert resolve_name(sidecars, catalog, "finlwt21") == expected
    assert resolve_name(sidecars, catalog, "FINLWT21") == expected
    assert resolve_name(sidecars, catalog, " Finlwt21 ") == expected


def test_a_shared_name_lists_every_table_core_first(sidecars, catalog):
    assert resolve_name(sidecars, catalog, "newid") == [
        ("fmli", "newid"),
        ("mtbi", "newid"),
    ]
    assert resolve_name(sidecars, catalog, "newid", table="mtbi") == [("mtbi", "newid")]


def test_core_tables_come_before_the_others(sidecars):
    assert [t.name for t in ordered_tables(sidecars)] == ["fmli", "mtbi"]


def test_an_unknown_name_suggests_the_closest(sidecars, catalog):
    with pytest.raises(UnknownVariable) as exc:
        resolve_name(sidecars, catalog, "finlwt12")
    assert exc.value.suggestions[0] == "finlwt21"
    assert len(exc.value.suggestions) <= 3


def test_an_unknown_name_in_one_table_names_the_table(sidecars, catalog):
    with pytest.raises(UnknownVariable) as exc:
        resolve_name(sidecars, catalog, "region", table="mtbi")
    assert "mtbi" in exc.value.message


def test_an_unknown_table_suggests_the_closest(sidecars):
    assert resolve_table(sidecars, "FMLI").name == "fmli"
    assert resolve_table(sidecars, "ce-interview-mtbi").name == "mtbi"
    with pytest.raises(UnknownTable) as exc:
        resolve_table(sidecars, "mtbx")
    assert exc.value.suggestions == ["mtbi"]


def test_a_diary_name_is_unknown_until_the_diary_list_is_built(sidecars, catalog):
    with pytest.raises(UnknownVariable):
        resolve_name(sidecars, catalog, "wk_wrkd")


def test_a_diary_name_is_refused_once_the_diary_list_is_built(
    sidecars, catalog, meta_data_dir
):
    ref = meta_data_dir / "ref"
    ref.mkdir()
    duckdb.connect().execute(
        "copy (select 'wk_wrkd' as variable_name) "
        f"to '{ref / 'diary_variables.parquet'}' (format parquet)"
    )
    with pytest.raises(OutOfScope) as exc:
        resolve_name(sidecars, catalog, "WK_WRKD")
    assert "Diary" in exc.value.message


def test_closest_names_are_few_and_ordered():
    pool = ["totexppq", "totexpcq", "region", "tottxpdx"]
    assert set(closest_names("totexpq", pool)) == {"totexpcq", "totexppq", "tottxpdx"}
    assert closest_names("totexpq", pool, n=1)[0] in ("totexpcq", "totexppq")
    assert closest_names("zzzzzz", pool) == []
