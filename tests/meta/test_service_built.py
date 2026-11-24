"""What the dictionary tables, `unmatched_codes` and the crosswalk add.

The same fixture set as `test_service.py`, with the reference tables written
as `ce-meta build` and `ce-ingest profile` leave them.
"""

import pytest

from ce_meta.catalog import DuckDBCatalog
from ce_meta.errors import CeMetaError, OutOfScope, UnknownTable, YearNotCovered
from ce_meta.history import load_history


def _names(search) -> list[tuple[str, str]]:
    return [(m.table, m.name) for m in search.matches]


def _build(notices) -> list[str]:
    return [n.text for n in notices if n.scope == "build"]


# ── the history itself ───────────────────────────────────────────────────────


def test_history_is_none_until_the_tables_are_built(meta_data_dir):
    assert load_history(DuckDBCatalog(meta_data_dir)) is None


def test_history_answers_by_year(built_service):
    history = built_service._history()
    assert history.first_year == 1984
    assert history.covering("fmli", "oldvar", 1990).description == "Dropped long ago"
    assert history.covering("fmli", "oldvar", 2025) is None
    assert history.latest("fmli", "oldvar").last_year == 2001
    assert history.find("FEDTAXX") == [("ntaxi", "fedtaxx")]
    assert history.find("fedtaxx", "fmli") == []
    assert [c.value for c in history.codes("fmli", "region", 1990)] == ["1", "5"]
    assert [c.value for c in history.codes("fmli", "region", 2025)] == [
        "1",
        "2",
        "3",
        "4",
        "C",
    ]
    assert history.years("fmli", "fincbtxm") == [2004]


# ── years on the answers ─────────────────────────────────────────────────────


def test_describe_variable_gains_its_years_and_loses_the_notice(built_service):
    detail = built_service.describe_variable("region")
    assert (detail.first_year, detail.last_year) == (1984, None)
    assert _build(detail.notices) == []
    assert detail.sources[-1] == "ref/variables and ref/codes, for the years"


def test_a_code_shows_its_year_only_when_it_says_something(built_service):
    codes = built_service.describe_variable("region").codes
    assert {c.value: c.first_year for c in codes} == {
        "1": None,
        "2": None,
        "3": None,
        "4": None,
        "C": 2025,  # added since the others
    }
    # the labels of the release come from the sidecar, which a person corrected
    assert codes[-1].label == "Combined (not a region)"


def test_describe_variable_for_another_year_uses_that_years_codes(built_service):
    detail = built_service.describe_variable("region", year=1990)
    assert [(c.value, c.label, c.last_year) for c in detail.codes] == [
        ("1", "Northeast", 1993),
        ("5", "Outside the regions", 1993),
    ]
    assert detail.codes_total == 2


def test_search_carries_the_years(built_service):
    match = built_service.search_variables("fincbtxm").matches[0]
    assert (match.first_year, match.last_year) == (2004, None)


def test_describe_variable_lists_values_that_are_not_codes(built_service):
    profile = built_service.describe_variable("region").profile
    assert profile.undocumented_values == []  # built, and nothing unlisted
    detail = built_service.describe_variable("state")
    assert detail.profile is None  # the fixture profile has no row for it
    # only this survey's rows are read
    assert built_service._undocumented("fmli", "state") == ["99"]


# ── years the dictionary does not reach ──────────────────────────────────────


def test_a_year_outside_the_dictionary_gives_the_nearest(built_service):
    with pytest.raises(YearNotCovered) as exc:
        built_service.describe_variable("region", year=1950)
    assert exc.value.suggestions == ["1984"]
    assert "does not reach 1950" in exc.value.message
    for call in (
        lambda: built_service.search_variables("region", year=2031),
        lambda: built_service.list_geographies("region", 2031),
        lambda: built_service.dictionary(2031),
    ):
        with pytest.raises(YearNotCovered) as exc:
            call()
        assert exc.value.suggestions == ["2025"]


def test_a_variable_that_did_not_exist_that_year_says_when_it_did(built_service):
    with pytest.raises(YearNotCovered) as exc:
        built_service.describe_variable("fincbtxm", year=1990)
    assert exc.value.message == "fmli.fincbtxm has no entry for 1990."
    assert exc.value.suggestions == ["2004"]
    assert built_service.search_variables("fincbtxm", year=1990).total == 0


# ── variables that are not in the loaded data ────────────────────────────────


def test_search_finds_a_dictionary_only_variable_below_the_loaded_ones(built_service):
    found = built_service.search_variables("total")
    assert _names(found) == [
        ("fmli", "totexpcq"),
        ("fmli", "totexppq"),
        ("mtbi", "cost"),
        ("fmli", "newvar"),
    ]
    last = found.matches[-1]
    assert last.in_loaded_data is False
    assert (last.usable_as, last.review) == ([], ["not in the loaded data"])
    assert (last.first_year, last.role) == (2020, None)


def test_a_search_by_use_skips_what_cannot_be_used(built_service):
    found = built_service.search_variables("total", usable_as="measure")
    assert ("fmli", "newvar") not in _names(found)


def test_search_in_a_past_year_and_in_a_table_that_is_not_loaded(built_service):
    assert _names(built_service.search_variables("dropped", year=1990)) == [
        ("fmli", "oldvar")
    ]
    assert built_service.search_variables("dropped").total == 0  # ended in 2001
    assert _names(built_service.search_variables("tax", table="ntaxi", year=2020)) == [
        ("ntaxi", "fedtaxx")
    ]
    with pytest.raises(UnknownTable):
        built_service.search_variables("tax", table="nowhere")


def test_describe_variable_from_the_dictionary_alone(built_service):
    detail = built_service.describe_variable("OLDVAR")
    assert (detail.name, detail.table, detail.in_loaded_data) == (
        "oldvar",
        "fmli",
        False,
    )
    assert (detail.first_year, detail.last_year) == (1984, 2001)
    assert detail.sidecar is None and detail.profile is None
    assert [(c.value, c.label) for c in detail.codes] == [("1", "Yes, long ago")]
    assert detail.review == ["not in the loaded data"]
    assert "not in the loaded data" in detail.notices[0].text
    assert "1984 to 2001" in detail.summary
    other = built_service.describe_variable("fedtaxx", table="ntaxi")
    assert (other.table, other.last_year) == ("ntaxi", 2023)


def test_a_diary_variable_is_refused(built_service):
    with pytest.raises(OutOfScope) as exc:
        built_service.describe_variable("WK_WRKD")
    assert "Diary" in exc.value.message


# ── geographies ──────────────────────────────────────────────────────────────


def test_list_geographies_gains_parents_and_notes(built_service):
    listing = built_service.list_geographies("state")
    by_code = {e.code: e for e in listing.entries}
    assert (by_code["06"].parent_division, by_code["06"].parent_region) == ("9", "4")
    assert by_code["06"].interviews == 5
    assert _build(listing.notices) == []
    region = {e.code: e for e in built_service.list_geographies("region").entries}
    assert region["C"].note == "not a region"
    assert "5" not in region  # a code of an earlier year


def test_list_geographies_lists_a_level_no_column_carries(built_service):
    listing = built_service.list_geographies("division")
    assert (listing.table, listing.column) == (None, None)
    assert [(e.code, e.parent_region, e.interviews) for e in listing.entries] == [
        ("6", "3", None),
        ("9", "4", None),
    ]
    assert "No sidecar column" in listing.notices[0].text
    assert listing.unlisted_interviews is None
    psu = built_service.list_geographies("psu")
    assert {e.code: e.note for e in psu.entries} == {
        "S11A": "spans MA, NH",
        "S49A": None,
    }
    assert psu.support == "unsupported"


def test_list_geographies_for_an_earlier_year_uses_its_codes(built_service):
    listing = built_service.list_geographies("region", 1990)
    assert [e.code for e in listing.entries] == ["5"]


# ── dictionary ───────────────────────────────────────────────────────────────


def test_dictionary_for_the_release_and_for_an_earlier_year(built_service):
    release = built_service.dictionary()
    assert list(release) == ["fmli", "mtbi"]
    region = next(m for m in release["fmli"] if m.name == "region")
    assert (region.first_year, region.role) == (1984, "dimension")

    past = built_service.dictionary(1990)
    assert list(past) == ["fmli", "mtbi"]
    assert [m.name for m in past["fmli"]] == [
        "fincbtax",
        "oldvar",
        "region",
        "state",
        "totexpcq",
    ]
    oldvar = next(m for m in past["fmli"] if m.name == "oldvar")
    assert oldvar.in_loaded_data is False
    assert built_service.dictionary(2020)["ntaxi"][0].name == "fedtaxx"


def test_dictionary_before_the_tables_are_built(meta_service):
    assert "fmli" in meta_service.dictionary(2025)
    with pytest.raises(CeMetaError):
        meta_service.dictionary(1990)
