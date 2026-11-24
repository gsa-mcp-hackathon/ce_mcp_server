"""Fixtures shared by the metadata service tests and the server's tool tests.

A small sidecar set and a small catalog are written to temp folders, so the
tests need neither the loaded data nor the BLS dictionary. The set is built to
pass the validator; a test that wants a failure breaks it on purpose.
"""

from pathlib import Path

import duckdb
import pytest
import yaml

from ce_meta.catalog import DuckDBCatalog
from ce_meta.service import MetadataService
from ce_meta.sidecars import (
    MASTER_FILE,
    VOCABULARY_FILE,
    VOCABULARY_SCHEMA_FILE,
    load_set,
    validate,
)

REAL_SIDECARS = Path(__file__).parents[1] / "sidecars"
FMLI = "ce-interview-fmli"
MTBI = "ce-interview-mtbi"
MASTER = {"id": "ce-interview-master", "version": "0.1.0"}


def _cp(facts: dict) -> list[dict]:
    return [{"property": k, "value": v} for k, v in facts.items()]


def column(name: str, title: str | None = None, **extra) -> dict:
    """A sidecar column. Keyword names starting with `ce_` become ce: facts."""
    facts = {"ce:sourceName": name.upper()}
    entry: dict = {"name": name, "logicalType": "string"}
    for key, value in extra.items():
        if key.startswith("ce_"):
            facts["ce:" + key[3:]] = value
        else:
            entry[key] = value
    if title:
        entry["businessName"] = title
        entry.setdefault("description", title + ".")
    entry["customProperties"] = _cp(facts)
    return entry


def table_doc(
    name: str, row: str, key: list[str], sentence: str, purpose: str, columns: list
) -> dict:
    return {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        "id": f"ce-interview-{name}",
        "version": "0.1.0",
        "description": {"purpose": purpose, "limitations": "See the master."},
        "customProperties": _cp({"ce:master": MASTER}),
        "schema": [
            {
                "name": name,
                "businessName": f"CE Interview {name} file",
                "description": sentence,
                "customProperties": _cp({"ce:rowRepresents": row, "ce:rowKey": key}),
                "properties": columns,
            }
        ],
    }


def _dollars(unit: str, **more) -> dict:
    return {
        "ce_role": "measure",
        "ce_scale": "ratio",
        "ce_unit": unit,
        "ce_level": "consumer_unit",
        **more,
    }


def small_set_docs() -> dict[str, dict]:
    """The fixture set, by file name."""
    vocab = yaml.safe_load((REAL_SIDECARS / VOCABULARY_FILE).read_text())
    quarterly = _dollars(
        "usd_quarterly", ce_additiveOver=["consumer_units", "months_in_scope"]
    )
    annual = _dollars("usd_annual", ce_additiveOver=["consumer_units"])
    imputed = {**annual, "ce_imputed": {"multiple": True, "iterations": 5}}
    fmli_columns = [
        column(
            "newid", "Interview identifier", ce_role="identifier", ce_scale="nominal"
        ),
        column(
            "cuid", "Consumer unit identifier", ce_role="identifier", ce_scale="nominal"
        ),
        column("finlwt21", "Final sampling weight", ce_role="weight", ce_scale="ratio"),
        column(
            "region",
            "Census region",
            ce_role="dimension",
            ce_scale="nominal",
            ce_geographyLevel="region",
            enum=[
                {"value": "1", "label": "Northeast"},
                {"value": "2", "label": "Midwest"},
                {"value": "3", "label": "South"},
                {"value": "4", "label": "West"},
                {"value": "C", "label": "Combined (not a region)"},
            ],
        ),
        column(
            "state",
            "State of residence",
            ce_role="dimension",
            ce_scale="nominal",
            ce_geographyLevel="state",
            ce_missingCodes=[""],
            enum=[
                {"value": "01", "label": "Alabama"},
                {"value": "06", "label": "California"},
            ],
        ),
        column(
            "cutenure",
            "Housing tenure",
            ce_role="dimension",
            ce_scale="nominal",
            enum=[
                {"value": "1", "label": "Owned with mortgage"},
                {"value": "4", "label": "Rented"},
            ],
        ),
        column(
            "totexppq",
            "Total expenditures last quarter",
            **quarterly,
            ce_calendarPair="totexpcq",
        ),
        column(
            "totexpcq",
            "Total expenditures this quarter",
            **quarterly,
            ce_calendarPair="totexppq",
        ),
        column("fincbtax", "Income before taxes, collected", **annual),
        *[
            column(
                f"fincbtx{i}",
                f"Income before taxes, version {i}",
                description=f"Imputation Iteration #{i} - FINCBTAX.",
                **imputed,
            )
            for i in range(1, 6)
        ],
        column("fincbtxm", "Income before taxes, imputed", **imputed),
        column(
            "fincbt_x",
            "Flag for fincbtax",
            ce_role="flag",
            ce_flagFor="fincbtax",
            ce_flagMeanings={"D": "valid", "T": "topcoded"},
        ),
        column(
            "fsalaryx",
            "Wage and salary income",
            tags=["needs-review", "review:verify"],
            **_dollars("VERIFY"),
        ),
        column("mystery", "Not yet classified", tags=["needs-review", "review:role"]),
        column("qintrvyr", "Interview year", ce_role="period", ce_scale="interval"),
    ]
    mtbi_columns = [
        column(
            "newid", "Interview identifier", ce_role="identifier", ce_scale="nominal"
        ),
        column(
            "ucc",
            "Universal Classification Code",
            ce_role="item_code",
            ce_scale="nominal",
            ce_codeHierarchy={"scheme": "ucc", "source": "VERIFY"},
            tags=["needs-review", "review:verify"],
            enum=[
                {"value": "210110", "label": "Rent of dwelling"},
                {"value": "790210", "label": "Groceries"},
                {"value": "790410", "label": "Dining out at restaurants"},
            ],
        ),
        column(
            "cost",
            "Total cost of item",
            **_dollars(
                "usd_monthly", ce_additiveOver=["consumer_units", "months_in_scope"]
            ),
        ),
        column("ref_yr", "Reference year", ce_role="period", ce_scale="interval"),
        column("ref_mo", "Reference month", ce_role="period", ce_scale="ordinal"),
    ]
    master = {
        "apiVersion": "v3.2.0",
        "kind": "DataContract",
        **MASTER,
        "context": {
            "constraints": [
                {
                    "id": "c-2025-gap",
                    "constraint": "State whether a 2025 estimate is as released.",
                }
            ]
        },
        "customProperties": _cp(
            {
                "ce:vocabulary": {"id": vocab["id"], "version": vocab["version"]},
                "ce:sourceRelease": 2025,
                "ce:targetPopulation": "US consumer units",
                "ce:design": {
                    "type": "rotating_panel",
                    "maxInterviews": 4,
                    "panelId": "cuid",
                },
                "ce:weight": {"table": FMLI, "column": "finlwt21"},
                "ce:replicateWeights": {"table": FMLI, "prefix": "wtrep", "count": 44},
                "ce:variance": {
                    "formula": "sum((theta_r - theta)^2) / (R * (1 - k)^2)",
                    "byRelease": [
                        {"releases": ">=2025", "method": "fay", "R": 44, "k": 0.5}
                    ],
                },
                "ce:referencePeriod": "three months before the interview month",
                "ce:breaks": [
                    {
                        "id": "BRK_2025",
                        "from": 2025,
                        "kind": "disruption",
                        "affects": "all",
                    }
                ],
                "ce:geographyLevels": {
                    "region": "reliable",
                    "division": "reliable",
                    "state": "limited",
                    "belowState": "unsupported",
                },
                "ce:tables": [
                    {
                        "sidecar": FMLI,
                        "version": "0.1.0",
                        "rowRepresents": "consumer_unit_interview",
                        "carriesWeight": True,
                    },
                    {
                        "sidecar": MTBI,
                        "version": "0.1.0",
                        "rowRepresents": "expenditure_item_month",
                        "carriesWeight": False,
                    },
                ],
                "ce:relationships": [
                    {
                        "parent": FMLI,
                        "child": MTBI,
                        "keys": {"parent": ["newid"], "child": ["newid"]},
                        "cardinality": "one_to_many",
                        "childRowsOptional": True,
                        "weightFrom": "parent",
                        "note": "No reported spending means no rows.",
                    },
                    {
                        "parent": FMLI,
                        "child": FMLI,
                        "keys": {"parent": ["cuid"], "child": ["cuid"]},
                        "cardinality": "same_unit",
                        "weightFrom": "self",
                    },
                ],
            }
        ),
    }
    return {
        VOCABULARY_FILE: vocab,
        MASTER_FILE: master,
        f"{FMLI}.odcs.yaml": table_doc(
            "fmli",
            "consumer_unit_interview",
            ["newid"],
            "One row per consumer unit per quarterly interview.",
            "Consumer unit characteristics, income and summary expenditures. More.",
            fmli_columns,
        ),
        f"{MTBI}.odcs.yaml": table_doc(
            "mtbi",
            "expenditure_item_month",
            ["newid", "ucc", "ref_yr", "ref_mo"],
            "One row per expenditure item (UCC) per reference month.",
            "Monthly expenditures by item code.",
            mtbi_columns,
        ),
    }


def write_set(folder: Path, docs: dict[str, dict]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, doc in docs.items():
        (folder / name).write_text(yaml.safe_dump(doc, sort_keys=False))
    schema = REAL_SIDECARS / VOCABULARY_SCHEMA_FILE
    (folder / VOCABULARY_SCHEMA_FILE).write_text(schema.read_text())
    return folder


# (survey_year, quarter_code, rows as (newid, region, state))
FMLI_PARTITIONS = [
    (2025, "1x", [("1", "1", "01"), ("2", "1", "06"), ("3", "C", "")]),
    (2025, "2", [("4", "2", "06"), ("5", "3", "06")]),
    (2025, "3", [("6", "3", "01"), ("7", "4", "")]),
    (2025, "4", [("8", "4", "06"), ("9", "4", "06")]),
    (2026, "1", [("10", "1", "01")]),
    # the other copy of 2025 quarter 1: it belongs to survey year 2024's set
    (
        2025,
        "1",
        [("1", "1", "01"), ("2", "1", "06"), ("3", "C", ""), ("11", "2", "06")],
    ),
]


def write_catalog(data_dir: Path) -> Path:
    """A registry, a profile and an `fmli` table, as the loader stores them."""
    con = duckdb.connect()
    meta = data_dir / "meta"
    meta.mkdir(parents=True)

    registry = [
        ("intrvw", "fmli", y, q, "loaded", len(rows), "2026-11-17 10:00:00+00")
        for y, q, rows in FMLI_PARTITIONS
    ]
    registry += [
        # a later failed attempt must not hide the good load
        ("intrvw", "fmli", 2025, "2", "failed", 0, "2026-11-18 10:00:00+00"),
        (
            "intrvw",
            "mtbi",
            2025,
            "2",
            "loaded_with_warnings",
            5,
            "2026-11-17 11:00:00+00",
        ),
        ("intrvw", "zzz", 2025, "2", "loaded", 7, "2026-11-17 11:00:00+00"),
        ("diary", "fmld", 2025, "2", "loaded", 9, "2026-11-17 11:00:00+00"),
    ]
    con.execute(
        'create table registry (survey varchar, "table" varchar, survey_year integer, '
        "quarter_code varchar, status varchar, row_count bigint, loaded_at timestamptz)"
    )
    con.executemany("insert into registry values (?, ?, ?, ?, ?, ?, ?)", registry)
    con.execute(
        f"copy registry to '{meta / 'dataset_registry.parquet'}' (format parquet)"
    )

    con.execute(
        'create table profile (survey varchar, "table" varchar, column_name varchar, '
        'data_type varchar, row_count bigint, null_share double, "min" varchar, '
        '"max" varchar, distinct_count bigint)'
    )
    con.executemany(
        "insert into profile values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("intrvw", "fmli", "region", "VARCHAR", 14, 0.0, "1", "C", 5),
            ("intrvw", "fmli", "newid", "VARCHAR", 14, 0.0, "1", "9", 11),
            ("intrvw", "fmli", "fincbtax", "DOUBLE", 14, 0.25, "-100.0", "900000.0", 9),
        ],
    )
    con.execute(f"copy profile to '{meta / 'column_profile.parquet'}' (format parquet)")

    for year, code, rows in FMLI_PARTITIONS:
        part = (
            data_dir
            / "intrvw"
            / "fmli"
            / f"survey_year={year}"
            / f"quarter_code={code}"
        )
        part.mkdir(parents=True)
        con.execute(
            "create or replace table part "
            "(newid varchar, region varchar, state varchar)"
        )
        con.executemany("insert into part values (?, ?, ?)", rows)
        con.execute(f"copy part to '{part / 'part-0.parquet'}' (format parquet)")
    con.close()
    return data_dir


@pytest.fixture
def meta_sidecar_dir(tmp_path) -> Path:
    return write_set(tmp_path / "sidecars", small_set_docs())


@pytest.fixture
def meta_data_dir(tmp_path) -> Path:
    return write_catalog(tmp_path / "data")


def make_service(sidecar_dir: Path, data_dir: Path) -> MetadataService:
    sidecars = load_set(sidecar_dir)
    return MetadataService(DuckDBCatalog(data_dir), sidecars, validate(sidecars), 2025)


@pytest.fixture
def meta_service(meta_sidecar_dir, meta_data_dir) -> MetadataService:
    return make_service(meta_sidecar_dir, meta_data_dir)


# ── the reference tables, as `ce-meta build` and `ce-ingest profile` leave them

REF_VARIABLES = [
    # table, variable_name, source_name, description, data_type, first, last, flag
    ("fmli", "region", "REGION", "Census region", "character", 1984, None, None),
    ("fmli", "state", "STATE", "State of residence", "character", 1984, None, None),
    ("fmli", "fincbtax", "FINCBTAX", "Income before taxes", "numeric", 1984, None,
     "fincbt_x"),
    ("fmli", "fincbtxm", "FINCBTXM", "Income before taxes, imputed", "numeric", 2004,
     None, None),
    ("fmli", "totexpcq", "TOTEXPCQ", "Total expenditures this quarter", "numeric",
     1984, None, None),
    # in the dictionary, not in the loaded data
    ("fmli", "oldvar", "OLDVAR", "Dropped long ago", None, 1984, 2001, None),
    ("fmli", "newvar", "NEWVAR", "Total income not yet loaded", None, 2020, None, None),
    ("ntaxi", "fedtaxx", "FEDTAXX", "Federal tax of the tax unit", None, 2013, 2023,
     None),
    ("mtbi", "ucc", "UCC", "Universal Classification Code", "character", 1984, None,
     None),
]  # fmt: skip
REF_CODES = [
    # table, variable_name, code_value, code_label, first, last
    ("fmli", "region", "1", "Northeast", 1996, None),
    ("fmli", "region", "2", "Midwest", 1996, None),
    ("fmli", "region", "3", "South", 1996, None),
    ("fmli", "region", "4", "West", 1996, None),
    ("fmli", "region", "C", "Combined", 2025, None),
    ("fmli", "region", "1", "Northeast", 1984, 1993),
    ("fmli", "region", "5", "Outside the regions", 1984, 1993),
    ("fmli", "oldvar", "1", "Yes, long ago", 1984, 2001),
]
REF_CROSSWALK = [
    # level, code, label, parent state, division and region, first, last, note
    ("region", "1", "Northeast", None, None, None, 1996, None, None),
    ("region", "2", "Midwest", None, None, None, 1996, None, None),
    ("region", "3", "South", None, None, None, 1996, None, None),
    ("region", "4", "West", None, None, None, 1996, None, None),
    ("region", "C", "Combined", None, None, None, 2025, None, "not a region"),
    ("region", "5", "Outside the regions", None, None, None, 1984, 1993, None),
    ("division", "6", "East South Central", None, None, "3", 2015, None, None),
    ("division", "9", "Pacific", None, None, "4", 2015, None, None),
    ("state", "01", "Alabama", None, "6", "3", 1984, None, None),
    ("state", "06", "California", None, "9", "4", 1984, None, None),
    ("psu", "S49A", "Los Angeles", "06", "9", "4", 2015, None, None),
    ("psu", "S11A", "Boston", None, "1", "1", 2015, None, "spans MA, NH"),
]


def write_ref(data_dir: Path) -> Path:
    con = duckdb.connect()
    ref = data_dir / "ref"
    ref.mkdir(parents=True, exist_ok=True)

    def save(name: str, columns: str, rows: list[tuple], folder: Path = ref) -> None:
        con.execute(f"create or replace table t ({columns})")
        marks = ", ".join("?" for _ in rows[0])
        con.executemany(f"insert into t values ({marks})", rows)
        con.execute(f"copy t to '{folder / (name + '.parquet')}' (format parquet)")

    save(
        "variables",
        '"table" varchar, variable_name varchar, source_name varchar, '
        "description varchar, data_type varchar, first_year integer, "
        "last_year integer, flag_name varchar",
        REF_VARIABLES,
    )
    save(
        "codes",
        '"table" varchar, variable_name varchar, code_value varchar, '
        "code_label varchar, first_year integer, last_year integer",
        REF_CODES,
    )
    save("diary_variables", "variable_name varchar", [("wk_wrkd",)])
    save(
        "geo_crosswalk",
        "level varchar, code varchar, label varchar, parent_state varchar, "
        "parent_division varchar, parent_region varchar, first_year integer, "
        "last_year integer, note varchar",
        REF_CROSSWALK,
    )
    save(
        "unmatched_codes",
        'survey varchar, "table" varchar, column_name varchar, "value" varchar, '
        "row_count bigint",
        [("intrvw", "fmli", "state", "99", 3), ("diary", "fmli", "state", "88", 1)],
        folder=data_dir / "meta",
    )
    con.close()
    return data_dir


@pytest.fixture
def built_service(meta_sidecar_dir, meta_data_dir) -> MetadataService:
    """The service once every reference table has been built."""
    return make_service(meta_sidecar_dir, write_ref(meta_data_dir))
