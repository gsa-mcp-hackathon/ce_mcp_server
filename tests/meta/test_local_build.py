"""The reference tables built from the real dictionary and the loaded release.

Marked `slow`: these read `data/parquet/` and `../sample_data/`, which are not
part of the source. They check the plan's acceptance layers on the real data:
dictionary coverage, sidecar agreement, and the crosswalk.
"""

from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
pytest.importorskip("openpyxl")

from ce_ingest.profile import LOADER_COLUMNS  # noqa: E402
from ce_ingest.registry import SOURCE_COLUMNS_TABLE  # noqa: E402
from ce_ingest.writers import ParquetWriter  # noqa: E402
from ce_meta.build.geo import build_crosswalk, check_geography  # noqa: E402
from ce_meta.build.reference import build_codes, build_variables  # noqa: E402
from ce_meta.sidecars import load_set  # noqa: E402
from ce_sidecars.dictionary import covers, read_history  # noqa: E402

ROOT = Path(__file__).parents[2]
DATA = ROOT / "data" / "parquet"
DICTIONARY = ROOT.parent / "sample_data" / "ce-pumd-interview-diary-dictionary-2.xlsx"
RELEASE = 2025

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (DATA / "intrvw" / "fmli").exists() or not DICTIONARY.exists(),
        reason="needs the loaded 2025 release and the BLS dictionary",
    ),
]


@pytest.fixture(scope="module")
def built():
    writer = ParquetWriter(DATA)
    history = read_history(DICTIONARY)
    stored = {table for _, table in writer.tables("intrvw")}
    variables = build_variables(
        history, writer.read_rows(SOURCE_COLUMNS_TABLE), stored, RELEASE
    )
    codes = build_codes(history, stored)
    return writer, stored, variables.to_pylist(), codes


def test_the_dictionary_tables_cover_the_whole_history(built):
    _, stored, variables, codes = built
    assert len(stored) == 48
    assert len(variables) == 3934 and codes.num_rows == 16656
    assert stored <= {v["table"] for v in variables}  # every stored table is there
    assert min(v["first_year"] for v in variables) == 1980


def test_every_stored_column_is_documented_a_loader_column_or_known_missing(built):
    writer, stored, variables, _ = built
    documented = set()
    for v in variables:
        if covers(v["first_year"], v["last_year"], RELEASE):
            documented.add((v["table"], v["variable_name"]))
            if v["flag_name"]:
                documented.add((v["table"], v["flag_name"]))
    sidecars = load_set(ROOT / "sidecars")
    missing = [
        (t.name, c.name)
        for t in sidecars.tables.values()
        for c in t.columns.values()
        if (t.name, c.name) not in documented and c.name not in LOADER_COLUMNS
    ]
    # 2,751 columns: all but these are in the dictionary for the release
    assert len(missing) == 92
    profile = writer.read_rows("column_profile")
    flagged = [r for r in profile.to_pylist() if r["undocumented"]]
    if flagged:  # filled once `ce-ingest profile` has run after `ce-meta build`
        assert {(r["table"], r["column_name"]) for r in flagged} == set(missing)


def test_sidecar_code_lists_agree_with_the_codes_table(built):
    _, _, _, codes = built
    lists: dict[tuple[str, str], set[str]] = {}
    for c in codes.to_pylist():
        if covers(c["first_year"], c["last_year"], RELEASE):
            lists.setdefault((c["table"], c["variable_name"]), set()).add(
                c["code_value"]
            )
    sidecars = load_set(ROOT / "sidecars")
    differences = []
    for t in sidecars.tables.values():
        for c in t.columns.values():
            if c.get("ce:role") == "flag" or c.name in LOADER_COLUMNS:
                continue  # flag codes and loader columns are not from the dictionary
            in_sidecar = {value for value, _ in c.codes}
            if in_sidecar != lists.get((t.name, c.name), set()):
                differences.append((t.name, c.name))
    assert differences == []


def test_the_crosswalk_agrees_with_the_loaded_data(built):
    writer, _, _, codes = built
    source = writer.duckdb_from("intrvw", "fmli")
    crosswalk = build_crosswalk(codes, source, RELEASE)
    rows = crosswalk.to_pylist()
    states = [
        r
        for r in rows
        if r["level"] == "state" and covers(r["first_year"], r["last_year"], RELEASE)
    ]
    assert all(r["parent_division"] and r["parent_region"] for r in states)
    psus = [
        r
        for r in rows
        if r["level"] == "psu" and covers(r["first_year"], r["last_year"], RELEASE)
    ]
    assert len(psus) == 23
    assert sum(1 for r in psus if r["note"] and "spans" in r["note"]) == 7
    assert sum(1 for r in psus if r["note"] and "divisions" in r["note"]) == 3

    check = check_geography(crosswalk, source, RELEASE)
    assert check.conflicts == [] and check.unresolved == []
    assert (check.checked, check.excluded) == (15452, 1302)
