"""The geographic crosswalk: `ref/geo_crosswalk`.

One row per region, division, state and PSU code and range of years, with its
label and its parents. Codes, labels and years come from the dictionary's
`codes` table. A state's division and region come from the Census Bureau's
definitions. A PSU's parents come from the data, and only when the data leaves
no doubt: a metropolitan area that spans states is not forced onto one.
"""

from dataclasses import dataclass, field

import duckdb
import pyarrow as pa

from ce_sidecars.dictionary import covers

CROSSWALK_TABLE = "geo_crosswalk"
GEO_TABLE = "fmli"  # the table whose columns carry the geography
LEVELS = ("region", "division", "state", "psu")
NOT_A_REGION = "C"

CROSSWALK_SCHEMA = pa.schema(
    [
        ("level", pa.string()),
        ("code", pa.string()),
        ("label", pa.string()),
        ("parent_state", pa.string()),
        ("parent_division", pa.string()),
        ("parent_region", pa.string()),
        ("first_year", pa.int32()),
        ("last_year", pa.int32()),
        ("note", pa.string()),
    ]
)

# Census divisions and the region each belongs to, by the codes BLS uses.
DIVISION_REGION: dict[str, str] = {
    "1": "1",  # New England        -> Northeast
    "2": "1",  # Middle Atlantic    -> Northeast
    "3": "2",  # East North Central -> Midwest
    "4": "2",  # West North Central -> Midwest
    "5": "3",  # South Atlantic     -> South
    "6": "3",  # East South Central -> South
    "7": "3",  # West South Central -> South
    "8": "4",  # Mountain           -> West
    "9": "4",  # Pacific            -> West
}

# The 50 states and the District of Columbia by FIPS code: postal
# abbreviation and Census division. Source: U.S. Census Bureau, "Census
# Regions and Divisions of the United States".
CENSUS_STATES: dict[str, tuple[str, str]] = {
    "09": ("CT", "1"), "23": ("ME", "1"), "25": ("MA", "1"), "33": ("NH", "1"),
    "44": ("RI", "1"), "50": ("VT", "1"),
    "34": ("NJ", "2"), "36": ("NY", "2"), "42": ("PA", "2"),
    "17": ("IL", "3"), "18": ("IN", "3"), "26": ("MI", "3"), "39": ("OH", "3"),
    "55": ("WI", "3"),
    "19": ("IA", "4"), "20": ("KS", "4"), "27": ("MN", "4"), "29": ("MO", "4"),
    "31": ("NE", "4"), "38": ("ND", "4"), "46": ("SD", "4"),
    "10": ("DE", "5"), "11": ("DC", "5"), "12": ("FL", "5"), "13": ("GA", "5"),
    "24": ("MD", "5"), "37": ("NC", "5"), "45": ("SC", "5"), "51": ("VA", "5"),
    "54": ("WV", "5"),
    "01": ("AL", "6"), "21": ("KY", "6"), "28": ("MS", "6"), "47": ("TN", "6"),
    "05": ("AR", "7"), "22": ("LA", "7"), "40": ("OK", "7"), "48": ("TX", "7"),
    "04": ("AZ", "8"), "08": ("CO", "8"), "16": ("ID", "8"), "30": ("MT", "8"),
    "32": ("NV", "8"), "35": ("NM", "8"), "49": ("UT", "8"), "56": ("WY", "8"),
    "02": ("AK", "9"), "06": ("CA", "9"), "15": ("HI", "9"), "41": ("OR", "9"),
    "53": ("WA", "9"),
}  # fmt: skip


def _psu_parents(fmli_source: str | None) -> dict[str, dict[str, list[str]]]:
    """Per PSU in the data: the distinct states, divisions and regions it has."""
    if fmli_source is None:
        return {}
    rows = (
        duckdb.connect()
        .execute(
            f"""
            select psu,
                   list(distinct state) filter (where state <> ''),
                   list(distinct division) filter (where division <> ''),
                   list(distinct region)
                       filter (where region <> '' and region <> '{NOT_A_REGION}')
            from {fmli_source}
            where psu is not null and psu <> ''
            group by 1
            """
        )
        .fetchall()
    )
    return {
        psu: {
            "state": sorted(states or []),
            "division": sorted(divisions or []),
            "region": sorted(regions or []),
        }
        for psu, states, divisions, regions in rows
    }


def _only(values: list[str]) -> str | None:
    return values[0] if len(values) == 1 else None


def build_crosswalk(codes: pa.Table, fmli_source: str | None, release: int) -> pa.Table:
    """Every geography code with its label, years and parents.

    `codes` is the dictionary's codes table; `fmli_source` is a DuckDB FROM
    expression for the loaded `fmli` table, or None when no data is loaded, in
    which case no PSU gets a parent.
    """
    in_data = _psu_parents(fmli_source)
    rows = []
    for r in codes.to_pylist():
        level = r["variable_name"]
        if r["table"] != GEO_TABLE or level not in LEVELS:
            continue
        code = r["code_value"]
        entry = {
            "level": level,
            "code": code,
            "label": r["code_label"],
            "parent_state": None,
            "parent_division": None,
            "parent_region": None,
            "first_year": r["first_year"],
            "last_year": r["last_year"],
            "note": None,
        }
        if level == "region":
            if code == NOT_A_REGION:
                entry["note"] = "not a region"
        elif level == "division":
            entry["parent_region"] = DIVISION_REGION.get(code)
        elif level == "state":
            known = CENSUS_STATES.get(code)
            if known:
                entry["parent_division"] = known[1]
                entry["parent_region"] = DIVISION_REGION[known[1]]
            else:
                entry["note"] = "not one of the 50 states or DC"
        elif not covers(r["first_year"], r["last_year"], release):
            entry["note"] = "not a PSU of the loaded release"
        elif code not in in_data:
            entry["note"] = "not in the loaded data"
        else:
            seen = in_data[code]
            entry["parent_state"] = _only(seen["state"])
            entry["parent_division"] = _only(seen["division"])
            entry["parent_region"] = _only(seen["region"])
            spans = []
            if len(seen["state"]) > 1:
                names = [CENSUS_STATES.get(s, (s,))[0] for s in seen["state"]]
                spans.append("spans " + ", ".join(names))
            if len(seen["division"]) > 1:
                spans.append("spans divisions " + ", ".join(seen["division"]))
            entry["note"] = "; ".join(spans) or None
        rows.append(entry)
    rows.sort(
        key=lambda e: (
            LEVELS.index(e["level"]),
            e["code"],
            -1 if e["first_year"] is None else e["first_year"],
        )
    )
    return pa.Table.from_pylist(rows, schema=CROSSWALK_SCHEMA)


@dataclass
class GeoCheck:
    """What the crosswalk's check against the loaded data found."""

    checked: int = 0  # records compared
    excluded: int = 0  # records left out: a blank value, or region C
    conflicts: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # values in no code list

    @property
    def ok(self) -> bool:
        return not self.conflicts


def check_geography(
    crosswalk: pa.Table,
    fmli_source: str,
    release: int,
    unmatched: set[tuple[str, str]] | None = None,
) -> GeoCheck:
    """Compare the crosswalk with the loaded `fmli` rows.

    Every value in the data must be a crosswalk code of the release or be
    listed in `unmatched` (`(column, value)` pairs from `unmatched_codes`). On
    records where state, division and region are all present and the region is
    not `C`, the state's division and region must equal the record's.
    """
    unmatched = unmatched or set()
    known: dict[str, set[str]] = {level: set() for level in LEVELS}
    state_parents: dict[str, tuple[str | None, str | None]] = {}
    for r in crosswalk.to_pylist():
        if not covers(r["first_year"], r["last_year"], release):
            continue
        known[r["level"]].add(r["code"])
        if r["level"] == "state":
            state_parents[r["code"]] = (r["parent_division"], r["parent_region"])

    con = duckdb.connect()
    out = GeoCheck()
    for level in LEVELS:
        values = con.execute(
            f"select distinct {level} from {fmli_source} "
            f"where {level} is not null and {level} <> ''"
        ).fetchall()
        for (value,) in values:
            if value not in known[level] and (level, value) not in unmatched:
                out.unresolved.append(f"{level} {value!r}")
    out.unresolved.sort()

    rows = con.execute(
        f"""
        select state, division, region, count(*) as n
        from {fmli_source}
        group by all
        """
    ).fetchall()
    for state, division, region, n in rows:
        if not state or not division or not region or region == NOT_A_REGION:
            out.excluded += n
            continue
        out.checked += n
        expected = state_parents.get(state)
        if expected is None or expected == (None, None):
            continue  # a state the crosswalk cannot place: reported as unresolved
        if expected != (division, region):
            out.conflicts.append(
                f"state {state}: {n} records have division {division}, region "
                f"{region}; the crosswalk has division {expected[0]}, region "
                f"{expected[1]}"
            )
    out.conflicts.sort()
    return out
