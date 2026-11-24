"""The sidecar generator: dictionary reading, per-column rules, overrides, and a
from-scratch run on the real 2025 fmli."""

from pathlib import Path

import openpyxl
import pyarrow as pa
import pytest
import yaml

from ce_ingest.derive import add_partition_columns
from ce_ingest.writers import ParquetWriter
from ce_sidecars.build import generate_table_sidecar
from ce_sidecars.dictionary import Code, FileDictionary, Variable, read_dictionary
from ce_sidecars.generate import StoredColumn, build_properties

REPO = Path(__file__).parents[2]
SIDECARS = REPO / "sidecars"
LANDING = REPO.parent / "sample_data"
DICTIONARY = LANDING / "ce-pumd-interview-diary-dictionary-2.xlsx"


def _custom(entry):
    return {cp["property"]: cp["value"] for cp in entry.get("customProperties", [])}


# ── dictionary reader ────────────────────────────────────────────────────────


@pytest.fixture
def xlsx(tmp_path: Path) -> Path:
    wb = openpyxl.Workbook()
    v = wb.active
    v.title = "Variables"
    v.append(
        ["Survey", "File", "Variable Name", "Variable description", "Formula ",
         "Flag name", "Section number", "Section description", "Section part",
         "First year", "First Quarter", "Last year", "Last quarter", "Comment"]
    )  # fmt: skip
    v.append(
        ["INTERVIEW", "FMLI", "FAM_SIZE", "Number of Members in CU", "", "FAM__IZE",
         1, "CU characteristics", "", 1984, 1, None, None, ""]
    )  # fmt: skip
    v.append(
        ["INTERVIEW", "FMLI", "OLDVAR", "Dropped long ago", "", "", 1, "", "",
         1984, 1, 2001, 4, ""]
    )  # fmt: skip
    v.append(
        ["INTERVIEW", "MEMI", "AGE", "Age", "", "", 1, "", "", 1984, 1, None, None, ""]
    )  # fmt: skip
    c = wb.create_sheet("Codes ")  # the real sheet name has a trailing space
    c.append(
        ["Survey", "File", "Variable ", "Code value", "Code description",
         "First year", "First quarter", "Last year", "Last quarter", "Comment"]
    )  # fmt: skip
    for row in (
        ["INTERVIEW", "FMLI", "REGION", "1", "Northeast", 1996, 1, None, None, ""],
        ["INTERVIEW", "FMLI", "REGION", "1", "Northeast", 1980, 1, 1981, 4, ""],
        ["INTERVIEW", "FMLI", "REGION", "C", "Combined", 2025, 1, None, None, ""],
        ["INTERVIEW", "FMLI", "REGION", "5", "Outside SMSA", 1980, 1, 1981, 4, ""],
    ):
        c.append(row)
    path = tmp_path / "dict.xlsx"
    wb.save(path)
    return path


def test_read_dictionary_current_only(xlsx):
    d = read_dictionary(xlsx, "fmli", 2025)
    assert set(d.variables) == {"fam_size"}  # OLDVAR ended, AGE is MEMI
    assert d.variables["fam_size"].flag == "fam__ize"  # normalized like the loader
    assert [c.value for c in d.codes["region"]] == ["1", "C"]  # one "1", no "5"
    assert d.flag_of() == {"fam__ize": "fam_size"}


def test_release_year_selects_codes(xlsx):
    codes = read_dictionary(xlsx, "fmli", 2024).codes["region"]
    assert [c.value for c in codes] == ["1"]


# ── per-column rules ─────────────────────────────────────────────────────────


def _dictionary() -> FileDictionary:
    d = FileDictionary(file="FMLI", release=2025)
    for name, desc, formula, flag in [
        ("newid", "Interview id", None, None),
        ("finlwt21", "Weight", None, None),
        ("wtrep05", "Replicate weight", None, None),
        ("fincbtxm", "Income before taxes", "SUM(A, B)", "fincb_xm"),
        ("region", "Region", None, None),
        ("inc_rank", "Rank", None, None),
        ("fincbtxi", "Imputation", None, None),
    ]:
        d.variables[name] = Variable(name, name.upper(), desc, formula, flag, None)
    d.codes["region"] = [Code("1", "Northeast"), Code("C", "Combined")]
    d.codes["fincbtxi"] = [Code("100", "None"), Code("2nn", "Blanks")]
    return d


def _cols(*specs):
    loader = {"cuid", "survey_year", "quarter", "quarter_code", "mo_scope"}
    return [StoredColumn(n, t, None if n in loader else n.upper()) for n, t in specs]


def _by_name(props):
    return {p["name"]: p for p in props}


def test_rules_per_column():
    columns = _cols(
        ("newid", "VARCHAR"), ("finlwt21", "DOUBLE"), ("wtrep05", "DOUBLE"),
        ("fincbtxm", "DOUBLE"), ("fincb_xm", "VARCHAR"), ("region", "VARCHAR"),
        ("inc_rank", "DOUBLE"), ("mystery", "DOUBLE"), ("cuid", "VARCHAR"),
        ("quarter_code", "VARCHAR"),
    )  # fmt: skip
    props, review = build_properties(
        columns, _dictionary(), row_key=["newid"], weight_column="finlwt21"
    )
    p = _by_name(props)
    assert [x["name"] for x in props] == [c.name for c in columns]
    assert all(x["id"] == x["name"] for x in props)
    # settled columns carry no tags; the ones under review are tagged
    assert "tags" not in p["newid"] and "tags" not in p["region"]
    assert p["mystery"]["tags"] == [
        "needs-review",
        "review:not-in-dictionary",
        "review:role",
    ]
    # "Income before taxes" reads as a dollar amount; its period isn't stated
    assert _custom(p["fincbtxm"])["ce:role"] == "measure"
    assert p["fincbtxm"]["tags"] == ["needs-review", "review:verify"]
    assert list(p["mystery"])[-2:] == ["tags", "customProperties"]
    assert (p["newid"]["primaryKey"], p["newid"]["required"]) == (True, True)
    assert p["finlwt21"]["required"] and p["finlwt21"]["quality"][0]["mustBe"] == 0
    assert _custom(p["wtrep05"]) == {
        "ce:sourceName": "WTREP05",
        "ce:role": "replicate_weight",
        "ce:replicateIndex": 5,
    }
    assert "Formula: SUM(A, B)" in p["fincbtxm"]["description"]
    assert _custom(p["fincb_xm"])["ce:flagFor"] == "fincbtxm"
    meanings = _custom(p["fincb_xm"])["ce:flagMeanings"]
    assert meanings["E"] == "allocated"
    # a code that states several things keeps them all; H is a blank, not a value
    assert meanings["W"] == ["allocated", "imputed", "topcoded"]
    assert meanings["G"] == ["allocated", "imputed"]
    assert meanings["H"] == "parent_record"
    assert [e["value"] for e in p["region"]["enum"]] == ["1", "C"]
    assert _custom(p["region"])["ce:role"] == "dimension"
    # loader columns come from the generator's fixed block
    assert _custom(p["cuid"]) == {"ce:role": "identifier", "ce:scale": "nominal"}
    assert [e["value"] for e in p["quarter_code"]["enum"]] == ["1x", "1", "2", "3", "4"]
    assert review.items["Role and scale not assigned"] == ["inc_rank", "mystery"]
    assert review.items["Not in the data dictionary"] == ["mystery"]


def test_overrides_merge_over_generated():
    overrides = {
        "region": {
            "description": "Mine",
            "customProperties": {"ce:geographyLevel": "region", "ce:role": "dimension"},
        },
        "inc_rank": {
            "customProperties": {"ce:role": "measure", "ce:topcoded": "VERIFY"}
        },
    }
    columns = _cols(("region", "VARCHAR"), ("inc_rank", "DOUBLE"))
    props, review = build_properties(columns, _dictionary(), overrides)
    p = _by_name(props)
    assert p["region"]["description"] == "Mine"
    assert [e["value"] for e in p["region"]["enum"]] == ["1", "C"]  # kept
    assert _custom(p["region"]) == {
        "ce:sourceName": "REGION",
        "ce:role": "dimension",
        "ce:scale": "nominal",
        "ce:geographyLevel": "region",
    }
    assert _custom(p["inc_rank"])["ce:role"] == "measure"
    assert "Role and scale not assigned" not in review.items  # settled by override
    assert review.items["Still VERIFY: ce:topcoded"] == ["inc_rank"]
    assert p["inc_rank"]["tags"] == ["needs-review", "review:verify"]


def test_override_tags_are_kept_beside_review_tags():
    overrides = {"inc_rank": {"tags": ["income"]}}
    props, _ = build_properties(_cols(("inc_rank", "DOUBLE")), _dictionary(), overrides)
    assert props[0]["tags"] == ["income", "needs-review", "review:role"]


def test_a_settled_top_coding_decision_leaves_the_review_list():
    columns = _cols(
        ("hours1", "DOUBLE"), ("hou__s1", "VARCHAR"),
        ("weeks1", "DOUBLE"), ("wee__s1", "VARCHAR"),
        ("jobs1", "DOUBLE"), ("job__s1", "VARCHAR"),
    )  # fmt: skip
    overrides = {
        "hours1": {"description": "Hours.", "customProperties": {"ce:topcoded": True}},
        "hou__s1": {"customProperties": {"ce:flagFor": "hours1"}},
        "weeks1": {"description": "Weeks.", "customProperties": {"ce:topcoded": False}},
        "wee__s1": {"customProperties": {"ce:flagFor": "weeks1"}},
        "jobs1": {
            "description": "Jobs.",
            "customProperties": {"ce:topcoded": "VERIFY"},
        },
        "job__s1": {"customProperties": {"ce:flagFor": "jobs1"}},
    }
    observed = {c: {"D", "T"} for c in ("hou__s1", "wee__s1", "job__s1")}
    props, review = build_properties(columns, _dictionary(), overrides, observed)
    # decided true or false: settled; VERIFY is not a decision
    assert review.items["Possibly top-coded (its flag shows T, U, V or W)"] == ["jobs1"]
    p = _by_name(props)
    assert "review:topcoded" not in (p["hours1"].get("tags") or [])
    assert "review:topcoded" not in (p["weeks1"].get("tags") or [])
    assert "review:topcoded" in p["jobs1"]["tags"]


def test_override_declares_a_flag_the_dictionary_misses():
    overrides = {
        "hours1": {"description": "Hours worked.", "customProperties": {}},
        "hou__s1": {"customProperties": {"ce:flagFor": "hours1"}},
    }
    columns = _cols(("hours1", "DOUBLE"), ("hou__s1", "VARCHAR"))
    observed = {"hou__s1": {"D", "T"}}
    props, review = build_properties(columns, _dictionary(), overrides, observed)
    p = _by_name(props)
    assert _custom(p["hou__s1"])["ce:role"] == "flag"
    assert _custom(p["hou__s1"])["ce:flagFor"] == "hours1"
    assert p["hou__s1"]["businessName"] == "Flag for hours1"
    # the description settles "not in the dictionary"; the flag shows top-coding
    assert "Not in the data dictionary" not in review.items
    assert review.items["Possibly top-coded (its flag shows T, U, V or W)"] == [
        "hours1"
    ]


def test_overrides_for_unstored_columns_are_refused():
    with pytest.raises(ValueError, match="aren't stored"):
        build_properties(_cols(("region", "VARCHAR")), _dictionary(), {"gone": {}})


def test_observed_values_drive_review():
    columns = _cols(
        ("fincb_xm", "VARCHAR"), ("region", "VARCHAR"), ("fincbtxi", "DOUBLE")
    )
    observed = {
        "fincb_xm": {"D", "T"},
        "region": {"1", "", "9"},
        "fincbtxi": {"100.0", "201.0", "305.0"},
    }
    props, review = build_properties(columns, _dictionary(), observed=observed)
    p = _by_name(props)
    # the flag shows T, so the value column is tagged, even though it isn't stored
    # here; region gets both the blank-value and the unknown-code tags
    assert "review:topcoded" not in p["fincb_xm"].get("tags", [])
    assert p["region"]["tags"] == [
        "needs-review",
        "review:missing-codes",
        "review:unknown-codes",
    ]
    assert "review:unknown-codes" in p["fincbtxi"]["tags"]
    assert review.items["Possibly top-coded (its flag shows T, U, V or W)"] == [
        "fincbtxm"
    ]
    assert review.items['Blank values: candidate for ce:missingCodes [""]'] == [
        "region"
    ]
    # 9 is not a region code; 305 doesn't fit "100" or "2nn"
    assert review.items["Stored values not in the dictionary's codes"] == [
        "region",
        "fincbtxi",
    ]


# ── a whole sidecar from scratch, on a small stored table ────────────────────


MASTER = {
    "id": "ce-interview-master",
    "version": "0.1.0",
    "customProperties": [
        {"property": "ce:weight",
         "value": {"table": "ce-interview-fmli", "column": "finlwt21"}},
        {"property": "ce:tables",
         "value": [{"sidecar": "ce-interview-fmli", "version": "1.0.0",
                    "rowRepresents": "consumer_unit_interview",
                    "carriesWeight": True}]},
    ],
}  # fmt: skip


@pytest.fixture
def stored(out_dir, make_src):
    writer = ParquetWriter(out_dir)
    src = make_src("intrvw25/fmli252")
    t = pa.table(
        {
            "newid": ["05666064", "05666074", "05666074"],
            "finlwt21": [1.0, 2.0, 3.0],
            "region": ["1", "C", "1"],
        }
    )
    writer.write_partition(src, add_partition_columns(t, src))
    return writer


def _generate(writer, master=MASTER, key=None):
    return generate_table_sidecar(
        writer, "intrvw", "fmli", _dictionary(), master, None, "1.0.0", key
    )


def test_from_scratch_header(stored):
    doc, review = _generate(stored, key=["newid", "region"])
    assert (doc["id"], doc["version"]) == ("ce-interview-fmli", "1.0.0")
    assert _custom(doc) == {
        "ce:master": {"id": "ce-interview-master", "version": "0.1.0"}
    }
    schema = doc["schema"][0]
    assert _custom(schema) == {
        "ce:rowRepresents": "consumer_unit_interview",
        "ce:rowKey": ["newid", "region"],
    }
    assert schema["description"] == "One row per consumer unit per quarterly interview."
    assert doc["context"]["constraints"][0]["id"] == "c-master-required"
    assert review.items["Table description not written yet"] == ["(table)"]


def test_not_unique_row_key_stops(stored):
    with pytest.raises(ValueError, match="not unique"):
        _generate(stored, key=["newid"])


def test_table_missing_from_master_stops(stored):
    master = {**MASTER, "customProperties": [MASTER["customProperties"][0]]}
    with pytest.raises(ValueError, match="not in the master"):
        _generate(stored, master=master, key=["newid"])


def test_no_row_key_stops(stored):
    with pytest.raises(ValueError, match="no row key"):
        _generate(stored)


# ── a from-scratch run on the 2025 fmli ──────────────────────────────────────


@pytest.mark.slow
@pytest.mark.skipif(not DICTIONARY.exists(), reason="no sample_data")
def test_generate_fmli_from_2025_release(tmp_path):
    from ce_ingest.filename import discover_files
    from ce_ingest.pipeline import load_file
    from ce_sidecars.build import load_yaml, stored_columns

    writer = ParquetWriter(tmp_path / "out")
    for f in discover_files(LANDING, "intrvw"):
        if f.table == "fmli":
            load_file(f.path, writer)
    master = load_yaml(SIDECARS / "ce-interview-master.odcs.yaml")
    overrides = load_yaml(SIDECARS / "overrides" / "ce-interview-fmli.yaml")
    dictionary = read_dictionary(DICTIONARY, "fmli", 2025)
    doc, review = generate_table_sidecar(
        writer, "intrvw", "fmli", dictionary, master, overrides, "0.4.0"
    )

    props = doc["schema"][0]["properties"]
    stored = [c.name for c in stored_columns(writer, "intrvw", "fmli")]
    assert [p["name"] for p in props] == stored  # every stored column, in order
    assert len({p["id"] for p in props}) == len(props)

    vocab = yaml.safe_load((SIDECARS / "vocabulary.yaml").read_text())["properties"]
    closed = {
        k: set(v["values"]) for k, v in vocab.items() if isinstance(v["values"], list)
    }
    names = set(stored)
    for p in props:
        for prop, value in _custom(p).items():
            assert prop in vocab and vocab[prop]["level"] == "column", (p["name"], prop)
            if prop in closed and isinstance(value, str) and value != "VERIFY":
                assert value in closed[prop], (p["name"], prop, value)
            if prop in ("ce:flagFor", "ce:calendarPair"):
                assert value in names, (p["name"], value)

    # The committed sidecar is exactly what the generator produces.
    assert load_yaml(SIDECARS / "ce-interview-fmli.odcs.yaml") == doc
    # inc_hrs1 and its flag are settled in the overrides (the dictionary's
    # entries for INC_HRS1 end by 2006)
    assert len(review.items["Not in the data dictionary"]) == 6
    by_name = {p["name"]: p for p in props}
    assert "fam__ize" in by_name and "fam_ize" not in by_name  # BLS's __ is kept
    assert _custom(by_name["inc__rs1"])["ce:flagFor"] == "inc_hrs1"


# ── columns every BLS file shares, the detailed expenditure template, the master


def test_structural_columns_get_their_role_from_the_name():
    columns = [
        StoredColumn(n, "VARCHAR", n.upper())
        for n in ("newid", "membno", "seqno", "alcno", "ucc", "ref_mo", "refyr")
    ] + [
        StoredColumn("qyear", "VARCHAR", "QYEAR"),
        StoredColumn("impnum", "DOUBLE", "IMPNUM"),
    ]
    props, review = build_properties(columns, FileDictionary("MTBI", 2025))
    facts = {p["name"]: _custom(p) for p in props}
    for name in ("membno", "seqno", "alcno", "impnum"):
        assert facts[name]["ce:role"] == "identifier", name
    assert (facts["ref_mo"]["ce:role"], facts["ref_mo"]["ce:scale"]) == (
        "period",
        "ordinal",
    )
    assert (facts["refyr"]["ce:role"], facts["refyr"]["ce:scale"]) == (
        "period",
        "interval",
    )
    assert facts["qyear"]["ce:role"] == "period"
    assert facts["ucc"]["ce:role"] == "item_code"
    assert facts["ucc"]["ce:codeHierarchy"] == {"scheme": "ucc", "source": "VERIFY"}
    # a VERIFY inside a structured value still puts the column on the review list
    assert review.items["Still VERIFY: ce:codeHierarchy"] == ["ucc"]
    assert "Role and scale not assigned" not in review.items


def test_item_code_role_wins_over_the_dictionary_code_list():
    d = FileDictionary("MTBI", 2025, codes={"ucc": [Code("830111", "Some item")]})
    props, _ = build_properties([StoredColumn("ucc", "VARCHAR", "UCC")], d)
    assert _custom(props[0])["ce:role"] == "item_code"
    assert props[0]["enum"] == [{"value": "830111", "label": "Some item"}]


def test_expn_template_and_dictionary_file():
    from ce_sidecars.build import dictionary_file, expn_template

    assert dictionary_file("expn_rnt") == "rnt" and dictionary_file("fmli") == "fmli"
    v = Variable("rtcompx", "RTCOMPX", "Rent", None, None, "Rented Living  Quarters")
    text = expn_template(
        "expn_rnt", FileDictionary("RNT", 2025, variables={"rtcompx": v})
    )
    assert '"Rented Living Quarters" (BLS file RNT)' in text["description"]["purpose"]
    assert "sum its rows to newid" in text["description"]["usage"]
    assert text["schema"]["businessName"].endswith("Rented Living Quarters")
    bare = expn_template("expn_zzz", FileDictionary("ZZZ", 2025))
    assert "one questionnaire section (BLS file ZZZ)" in bare["description"]["purpose"]


MASTER_TEXT = """\
customProperties:
  # ── Table list
  - property: ce:tables
    value:
      - {sidecar: ce-interview-fmli, version: 0.4.0, carriesWeight: true}

  # ── Relationships
  - property: ce:relationships
    value:
      - parent: ce-interview-fmli
        child: ce-interview-fmli
        keys: {parent: [cuid], child: [cuid]}
        cardinality: same_unit
        weightFrom: self
        note: >
          The interviews of one consumer unit.

tags: [ 'bls' ]
"""


def test_sync_master_adds_changes_and_removes_only_its_own_blocks():
    from ce_sidecars.master import sync_master

    once = sync_master(MASTER_TEXT, {"expn_rnt": "0.1.0", "expn_apa": "0.2.0"})
    doc = yaml.safe_load(once)
    facts = {cp["property"]: cp["value"] for cp in doc["customProperties"]}
    assert [t["sidecar"] for t in facts["ce:tables"]] == [
        "ce-interview-fmli", "ce-interview-expn_apa", "ce-interview-expn_rnt"
    ]  # fmt: skip
    assert facts["ce:tables"][1]["version"] == "0.2.0"
    assert facts["ce:tables"][2] == {
        "sidecar": "ce-interview-expn_rnt", "version": "0.1.0",
        "rowRepresents": "expn_record", "carriesWeight": False,
    }  # fmt: skip
    rel = facts["ce:relationships"][-1]
    assert (rel["parent"], rel["child"]) == (
        "ce-interview-fmli",
        "ce-interview-expn_rnt",
    )
    assert rel["keys"] == {"parent": ["newid"], "child": ["newid"]}
    assert (rel["weightFrom"], rel["childRowsOptional"]) == ("parent", True)
    assert doc["tags"] == ["bls"]

    assert sync_master(once, {"expn_rnt": "0.1.0", "expn_apa": "0.2.0"}) == once
    fewer = yaml.safe_load(sync_master(once, {"expn_rnt": "0.1.0"}))
    facts = {cp["property"]: cp["value"] for cp in fewer["customProperties"]}
    assert len(facts["ce:tables"]) == 2 and len(facts["ce:relationships"]) == 2
    assert (
        sync_master(once, {}) == MASTER_TEXT
    )  # nothing left but the hand-written part

    with pytest.raises(ValueError, match="not detailed expenditure tables"):
        sync_master(MASTER_TEXT, {"fmli": "0.4.0"})


def test_expn_table_takes_its_row_key_and_text_from_the_template(out_dir, make_src):
    """With no overrides file, an expn table still gets a key and a description."""
    writer = ParquetWriter(out_dir)
    src = make_src("intrvw25/fmli252")  # any stored table; the master calls it expn
    t = pa.table(
        {
            "newid": ["05666064", "05666064", "05666074"],
            "seqno": [1.0, 2.0, 1.0],
            "alcno": [0.0, 0.0, 0.0],
            "qyear": ["20252", "20252", "20252"],
        }
    )
    writer.write_partition(src, add_partition_columns(t, src))
    master = {
        "id": "ce-interview-master",
        "version": "0.1.0",
        "customProperties": [
            {"property": "ce:tables",
             "value": [{"sidecar": "ce-interview-fmli", "version": "0.1.0",
                        "rowRepresents": "expn_record", "carriesWeight": False}]},
        ],
    }  # fmt: skip
    section = Variable(
        "seqno", "SEQNO", "Sequence Number", None, None, "Rented Quarters"
    )
    dictionary = FileDictionary("RNT", 2025, variables={"seqno": section})
    doc, review = generate_table_sidecar(
        writer, "intrvw", "fmli", dictionary, master, None, "0.1.0"
    )
    schema = doc["schema"][0]
    assert _custom(schema)["ce:rowKey"] == ["newid", "seqno", "alcno"]
    assert _custom(schema)["ce:rowRepresents"] == "expn_record"
    assert schema["tags"] == ["survey", "detailed-expenditure"]
    assert '"Rented Quarters" (BLS file RNT)' in doc["description"]["purpose"]
    assert "Table description not written yet" not in review.items
    facts = {p["name"]: _custom(p) for p in schema["properties"]}
    assert facts["seqno"]["ce:role"] == "identifier"
    assert facts["qyear"]["ce:role"] == "period"
