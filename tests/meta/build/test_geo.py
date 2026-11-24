"""The geographic crosswalk (`ce_meta.build.geo`)."""

from ce_meta.build.geo import (
    CENSUS_STATES,
    CROSSWALK_SCHEMA,
    DIVISION_REGION,
    build_crosswalk,
    check_geography,
)
from ce_meta.build.reference import build_codes
from ce_sidecars.dictionary import read_history

STORED = {"fmli", "expn_rnt"}


def _crosswalk(dictionary, stored):
    codes = build_codes(read_history(dictionary), STORED)
    source = stored.duckdb_from("intrvw", "fmli")
    return build_crosswalk(codes, source, 2025), source


def _by(table) -> dict:
    return {(r["level"], r["code"]): r for r in table.to_pylist()}


def test_the_state_table_covers_the_fifty_states_and_dc():
    assert len(CENSUS_STATES) == 51
    assert len({abbr for abbr, _ in CENSUS_STATES.values()}) == 51
    assert {division for _, division in CENSUS_STATES.values()} == set(DIVISION_REGION)
    assert CENSUS_STATES["06"] == ("CA", "9") and CENSUS_STATES["11"] == ("DC", "5")
    by_region: dict[str, int] = {}
    for _, division in CENSUS_STATES.values():
        region = DIVISION_REGION[division]
        by_region[region] = by_region.get(region, 0) + 1
    assert by_region == {"1": 9, "2": 12, "3": 17, "4": 13}


def test_states_and_divisions_get_their_census_parents(dictionary, stored):
    crosswalk, _ = _crosswalk(dictionary, stored)
    assert crosswalk.schema == CROSSWALK_SCHEMA
    rows = _by(crosswalk)
    california = rows[("state", "06")]
    assert (california["parent_division"], california["parent_region"]) == ("9", "4")
    assert rows[("division", "5")]["parent_region"] == "3"
    assert rows[("state", "72")]["note"] == "not one of the 50 states or DC"
    assert rows[("state", "72")]["parent_division"] is None
    assert rows[("region", "C")]["note"] == "not a region"
    assert rows[("region", "1")]["note"] is None


def test_a_psu_gets_parents_only_when_the_data_leaves_no_doubt(dictionary, stored):
    rows = _by(_crosswalk(dictionary, stored)[0])
    los_angeles = rows[("psu", "S49A")]
    assert (
        los_angeles["parent_state"],
        los_angeles["parent_division"],
        los_angeles["parent_region"],
        los_angeles["note"],
    ) == ("06", "9", "4", None)
    boston = rows[("psu", "S11A")]
    assert boston["parent_state"] is None  # it spans two states
    assert boston["parent_division"] == "1"
    assert boston["note"] == "spans MA, NH"
    washington = rows[("psu", "S35A")]
    assert washington["parent_state"] is None  # its only row has a blank state
    assert washington["parent_division"] == "5"
    assert rows[("psu", "A109")]["note"] == "not a PSU of the loaded release"


def test_without_data_no_psu_gets_a_parent(dictionary):
    codes = build_codes(read_history(dictionary), STORED)
    rows = _by(build_crosswalk(codes, None, 2025))
    assert rows[("psu", "S49A")]["parent_state"] is None
    assert rows[("psu", "S49A")]["note"] == "not in the loaded data"
    assert rows[("state", "06")]["parent_region"] == "4"


def test_check_geography_counts_what_it_leaves_out(dictionary, stored):
    crosswalk, source = _crosswalk(dictionary, stored)
    check = check_geography(crosswalk, source, 2025)
    assert check.ok
    assert (check.checked, check.excluded) == (4, 2)  # region C, and a blank state
    assert check.conflicts == [] and check.unresolved == []


def test_check_geography_reports_a_conflict_and_an_unknown_value(
    dictionary, stored, make_src
):
    import pyarrow as pa

    from ce_ingest.derive import add_partition_columns

    src = make_src("intrvw25/fmli253")
    bad = pa.table(
        {
            "newid": ["00000071", "00000081"],
            "region": ["1", "4"],  # California is in the West, not the Northeast
            "division": ["1", "9"],
            "state": ["06", "06"],
            "psu": ["", "S99Z"],  # a PSU no code list has
        }
    )
    stored.write_partition(src, add_partition_columns(bad, src))
    crosswalk, source = _crosswalk(dictionary, stored)
    check = check_geography(crosswalk, source, 2025)
    assert not check.ok
    assert len(check.conflicts) == 1 and "state 06: 1 records" in check.conflicts[0]
    assert check.unresolved == ["psu 'S99Z'"]
    listed = check_geography(crosswalk, source, 2025, unmatched={("psu", "S99Z")})
    assert listed.unresolved == []
