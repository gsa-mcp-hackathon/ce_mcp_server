"""The storage switch (`ce_meta.catalog`)."""

import pytest

from ce_meta.catalog import DuckDBCatalog, get_catalog
from ce_meta.config import MetaSettings
from ce_meta.errors import CatalogMissing


@pytest.fixture
def catalog(meta_data_dir):
    return DuckDBCatalog(meta_data_dir)


def test_has_tells_what_is_built(catalog):
    assert catalog.has("meta", "dataset_registry")
    assert catalog.has("raw", "fmli")
    assert not catalog.has("raw", "memi")
    assert not catalog.has("ref", "codes")
    assert not catalog.has("meta", "unmatched_codes")


def test_a_missing_table_names_the_command_that_builds_it(catalog):
    with pytest.raises(CatalogMissing) as exc:
        catalog.source("ref", "codes")
    assert "ce-meta build" in exc.value.message
    with pytest.raises(CatalogMissing) as exc:
        catalog.source("raw", "memi")
    assert "ce-ingest load" in exc.value.message


def test_rows_are_dicts_and_values_are_parameters(catalog):
    source = catalog.source("meta", "dataset_registry")
    rows = catalog.rows(
        f'select "table", count(*) as n from {source} where survey = ? group by 1',
        ["intrvw"],
    )
    assert {r["table"]: r["n"] for r in rows} == {"fmli": 7, "mtbi": 1, "zzz": 1}


def test_a_raw_table_reads_its_partition_columns_with_their_types(catalog):
    source = catalog.source("raw", "fmli")
    rows = catalog.rows(
        f"select distinct survey_year, quarter_code from {source} order by 1, 2"
    )
    assert (2025, "1x") in {(r["survey_year"], r["quarter_code"]) for r in rows}
    assert all(isinstance(r["quarter_code"], str) for r in rows)


def test_get_catalog_follows_the_backend(tmp_path):
    settings = MetaSettings(data_dir=tmp_path)
    assert isinstance(get_catalog(settings), DuckDBCatalog)
    with pytest.raises(NotImplementedError):
        get_catalog(MetaSettings(data_dir=tmp_path, backend="warehouse"))
