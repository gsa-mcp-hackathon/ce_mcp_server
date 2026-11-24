"""The lookup methods of the metadata service (`ce_meta.service`).

Run on the small fixture set and catalog of `tests/conftest.py`. No dictionary
table, `unmatched_codes` or crosswalk is built there, which is the state the
lookup core must work in.
"""

import copy

import pytest

from ce_meta.errors import CeMetaError, UnknownTable, UnknownVariable
from ce_meta.sidecars import MASTER_FILE
from tests.conftest import FMLI, make_service, small_set_docs, write_set


def _build(notices) -> list[str]:
    return [n.text for n in notices if n.scope == "build"]


# ── describe_survey, weighting_method ────────────────────────────────────────


def test_describe_survey_gives_each_fact_with_what_follows(meta_service):
    facts = meta_service.describe_survey()
    assert facts.release == 2025
    assert facts.weight.value == {"table": FMLI, "column": "finlwt21"}
    assert "population estimate" in facts.weight.implies
    assert facts.geography_levels.value["state"] == "limited"
    assert facts.constraints == ["State whether a 2025 estimate is as released."]
    assert "finlwt21 on fmli" in facts.summary
    assert facts.notices == []


def test_weighting_method_reads_the_variance_from_the_master(meta_service):
    method = meta_service.weighting_method()
    assert method.replicate_weights.value["count"] == 44
    assert method.variance.value["byRelease"][0]["k"] == 0.5
    assert "replicate weight" in method.replicate_weights.implies


# ── list_datasets ────────────────────────────────────────────────────────────


def test_list_datasets_counts_a_year_set_once(meta_service):
    listing = meta_service.list_datasets()
    by_table = {d.table: d for d in listing.datasets}
    # 3 + 2 + 2 + 2 + 1: the `1` copy of 2025 quarter 1 (4 rows) is not in the set
    assert by_table["fmli"].rows == 10
    assert listing.year == 2025
    assert [d.table for d in listing.datasets] == ["fmli", "mtbi", "zzz"]


def test_list_datasets_counts_the_earlier_year_from_its_own_files(meta_service):
    by_table = {d.table: d for d in meta_service.list_datasets(2024).datasets}
    assert by_table["fmli"].rows == 4  # only the 2025 `1` file belongs to 2024
    assert by_table["mtbi"].rows is None  # nothing loaded for that year: not zero


def test_list_datasets_uses_the_last_good_load(meta_service):
    listing = meta_service.list_datasets()
    fmli = next(d for d in listing.datasets if d.table == "fmli")
    assert fmli.rows == 10  # the later failed attempt on 2025/2 did not zero it
    assert listing.loaded_at.isoformat() == "2026-11-17T11:00:00+00:00"


def test_list_datasets_names_a_table_without_a_sidecar(meta_service):
    by_table = {d.table: d for d in meta_service.list_datasets().datasets}
    assert by_table["zzz"].has_sidecar is False
    assert by_table["zzz"].row_represents is None
    assert by_table["fmli"].has_sidecar is True
    assert by_table["fmli"].row_represents == "consumer_unit_interview"
    assert by_table["fmli"].purpose == (
        "Consumer unit characteristics, income and summary expenditures."
    )


def test_list_datasets_shows_partitions_only_where_they_differ(meta_service):
    listing = meta_service.list_datasets()
    by_table = {d.table: d for d in listing.datasets}
    assert listing.partitions == ["2025/2"]  # what most tables hold
    assert by_table["mtbi"].partitions is None
    assert by_table["fmli"].partitions == [
        "2025/1",
        "2025/1x",
        "2025/2",
        "2025/3",
        "2025/4",
        "2026/1",
    ]


def test_list_datasets_without_a_registry_still_answers(meta_sidecar_dir, tmp_path):
    service = make_service(meta_sidecar_dir, tmp_path / "nothing")
    listing = service.list_datasets()
    assert [d.table for d in listing.datasets] == ["fmli", "mtbi"]
    assert all(d.rows is None for d in listing.datasets)
    assert "ce-ingest load" in _build(listing.notices)[0]


# ── describe_table ───────────────────────────────────────────────────────────


def test_describe_table_for_the_table_that_carries_the_weight(meta_service):
    detail = meta_service.describe_table("fmli")
    assert detail.carries_weight is True
    assert detail.weight_route is None
    assert detail.same_unit_keys == ["cuid"]
    assert detail.row_key == ["newid"]
    assert detail.period_columns == ["qintrvyr"]
    assert detail.columns_by_role["not assigned"] == 1
    assert detail.columns_by_role["measure"] == 10
    assert detail.columns_needing_review == 2
    assert "It carries the weight." in detail.summary


def test_describe_table_gives_a_child_its_route_to_the_weight(meta_service):
    detail = meta_service.describe_table("ce-interview-mtbi")
    route = detail.weight_route
    assert (route.parent, route.parent_keys, route.child_keys) == (
        "fmli",
        ["newid"],
        ["newid"],
    )
    assert route.child_rows_optional is True
    assert detail.carries_weight is False
    assert detail.same_unit_keys == []
    assert "counts as zero" in detail.summary
    assert detail.row_sentence == (
        "One row per expenditure item (UCC) per reference month."
    )


def test_describe_table_says_when_the_description_is_a_placeholder(
    meta_sidecar_dir, meta_data_dir
):
    docs = small_set_docs()
    docs[f"{FMLI}.odcs.yaml"]["description"]["purpose"] = (
        "The FMLI table of the BLS Consumer Expenditure Interview Survey "
        "public-use microdata, as loaded."
    )
    service = make_service(write_set(meta_sidecar_dir, docs), meta_data_dir)
    detail = service.describe_table("fmli")
    assert detail.description_written is False
    assert detail.purpose is None
    assert any("not written" in n.text for n in detail.notices)


def test_describe_table_refuses_an_unknown_table(meta_service):
    with pytest.raises(UnknownTable) as exc:
        meta_service.describe_table("fmly")
    assert exc.value.suggestions == ["fmli"]


# ── search_variables ─────────────────────────────────────────────────────────


def _names(search) -> list[str]:
    return [m.name for m in search.matches]


def test_search_ranks_an_exact_name_first(meta_service):
    assert _names(meta_service.search_variables("fincbtax"))[0] == "fincbtax"
    assert _names(meta_service.search_variables("FINCBTAX"))[0] == "fincbtax"


def test_search_ranks_name_then_description_then_code_label(meta_service):
    found = meta_service.search_variables("rent")
    # no name holds "rent"; "Rented" and "Rent of dwelling" are code labels
    assert _names(found) == ["cutenure", "ucc"]
    assert [c.value for c in found.matches[0].matched_codes] == ["4"]
    assert found.matches[1].matched_codes[0].value == "210110"


def test_search_matches_words_at_the_start_of_a_word(meta_service):
    # "rent" must not match "curRENT" or "paRENT"; "expend" matches "expenditures"
    assert _names(meta_service.search_variables("expend")) == ["totexpcq", "totexppq"]
    assert meta_service.search_variables("xpend").total == 0


def test_search_puts_columns_holding_every_word_first(meta_service):
    found = meta_service.search_variables("income before taxes")
    assert _names(found)[:2] == ["fincbtxm", "fincbtax"]
    assert all(not m.lacks for m in found.matches[:2])


def test_search_shows_the_closest_columns_when_none_holds_every_word(meta_service):
    """A query a person would type, longer than any one column's words, still
    finds the column: the closest matches come back with what each lacks."""
    found = meta_service.search_variables("income groceries")
    assert _names(found) == ["ucc"]  # "Groceries" is one of its code labels
    assert found.matches[0].lacks == ["income"]
    assert found.matches[0].matched_codes[0].value == "790210"
    assert found.summary.startswith("No column holds every word of 'income groceries'")
    # a single word that matches nothing still finds nothing, and says what to try
    nothing = meta_service.search_variables("brand")
    assert nothing.total == 0 and "Try another word" in nothing.summary


def test_search_reads_a_query_across_the_description_and_one_code_label(meta_service):
    """ "Rented housing" is no column's title: "housing" is in the tenure
    column's title and "Rented" is one of its codes."""
    found = meta_service.search_variables("rented housing tenure")
    assert _names(found)[0] == "cutenure" and not found.matches[0].lacks
    assert [c.value for c in found.matches[0].matched_codes] == ["4"]


def test_search_weighs_a_rare_word_above_common_ones(meta_service):
    """ "Households that pay for groceries": only "groceries" is rare, and it
    alone brings the item-code column back; filler words are ignored."""
    found = meta_service.search_variables("the income of households with groceries")
    assert _names(found)[0] == "ucc"
    assert "the" not in found.matches[0].lacks and "of" not in found.matches[0].lacks


def test_search_does_not_piece_a_match_together_from_several_code_labels(meta_service):
    """ "Rent" and "groceries" are two different codes of ucc: together they
    are not one match, though each alone is half the query."""
    found = meta_service.search_variables("rent groceries")
    match = next(m for m in found.matches if m.name == "ucc")
    assert match.lacks in (["rent"], ["groceries"])


def test_search_finds_other_forms_of_a_word_after_the_word_itself(meta_service):
    assert _names(meta_service.search_variables("expenditure")) == [
        "totexpcq",
        "totexppq",
    ]
    assert _names(meta_service.search_variables("classified")) == ["mystery", "ucc"]


def test_search_puts_usable_columns_before_flags(meta_service):
    found = _names(meta_service.search_variables("fincbt"))
    assert found[-1] == "fincbt_x"
    assert set(found[:-1]) == {
        "fincbtax",
        "fincbtxm",
        *[f"fincbtx{i}" for i in range(1, 6)],
    }


def test_search_by_use_never_offers_a_flag_weight_or_identifier(meta_service):
    for usage in ("measure", "grouping", "filter"):
        for query in ("fincbt", "newid", "finlwt21", "flag", "identifier"):
            for m in meta_service.search_variables(query, usable_as=usage).matches:
                assert usage in m.usable_as
                assert m.role not in ("flag", "weight", "identifier")


def test_search_by_use_keeps_a_measure_under_review_with_its_note(meta_service):
    found = meta_service.search_variables("salary", usable_as="measure")
    assert _names(found) == ["fsalaryx"]
    assert found.matches[0].unit == "VERIFY"
    assert "unit under review" in found.matches[0].review


def test_search_leaves_out_a_column_with_no_role_when_a_use_is_asked(meta_service):
    assert _names(meta_service.search_variables("classified"))[0] == "mystery"
    by_use = meta_service.search_variables("classified", usable_as="filter")
    assert "mystery" not in _names(by_use)


def test_search_in_one_table_and_the_limit(meta_service):
    assert _names(meta_service.search_variables("newid", table="mtbi")) == ["newid"]
    found = meta_service.search_variables("income", limit=2)
    assert len(found.matches) == 2 and found.total == 8
    assert "showing 2" in found.summary
    assert len(meta_service.search_variables("income", limit=0).matches) == 1


def test_search_refuses_an_empty_query(meta_service):
    with pytest.raises(CeMetaError):
        meta_service.search_variables("   ")


def test_search_for_another_year_says_history_is_not_built(meta_service):
    assert _build(meta_service.search_variables("region").notices) == []
    notices = _build(meta_service.search_variables("region", year=2019).notices)
    assert len(notices) == 1 and "ce-meta build" in notices[0]


# ── describe_variable ────────────────────────────────────────────────────────


def test_describe_variable_joins_the_sidecar_and_the_profile(meta_service):
    detail = meta_service.describe_variable("REGION")
    assert (detail.name, detail.table, detail.role) == ("region", "fmli", "dimension")
    assert [c.value for c in detail.codes] == ["1", "2", "3", "4", "C"]
    assert detail.codes_total == 5
    assert detail.usable_as == ["grouping", "filter"]
    assert detail.data_type == "VARCHAR"
    assert detail.profile.distinct_count == 5
    assert detail.profile.undocumented_values is None
    assert detail.sidecar.facts["ce:geographyLevel"] == "region"
    assert any(c.startswith("ce:geographyLevel:") for c in detail.caveats)
    assert not any(c.startswith("ce:sourceName") for c in detail.caveats)
    assert detail.sources[0] == f"{FMLI} 0.1.0"
    assert "region is a dimension on the fmli table" in detail.summary


def test_describe_variable_says_what_is_not_built_yet(meta_service):
    built = _build(meta_service.describe_variable("region").notices)
    assert any("ref/variables" in t for t in built)
    assert any("meta/unmatched_codes" in t for t in built)
    # a column with no codes has no unlisted values to wait for
    built = _build(meta_service.describe_variable("fincbtax").notices)
    assert not any("unmatched_codes" in t for t in built)


def test_describe_variable_names_the_pair_the_flag_and_the_other_tables(meta_service):
    assert meta_service.describe_variable("totexpcq").calendar_pair == "totexppq"
    assert meta_service.describe_variable("fincbtax").flag_column == "fincbt_x"
    assert meta_service.describe_variable("region").flag_column is None
    assert meta_service.describe_variable("newid").also_in == ["mtbi"]
    assert meta_service.describe_variable("newid", table="mtbi").also_in == []


def test_describe_variable_gives_the_imputed_set_from_any_member(meta_service):
    expected = {
        "base": "fincbtax",
        "versions": [f"fincbtx{i}" for i in range(1, 6)],
        "mean": "fincbtxm",
        "version_column": None,
    }
    for name in ("fincbtax", "fincbtx3", "fincbtxm"):
        assert meta_service.describe_variable(name).imputed_set.model_dump() == expected
    assert meta_service.describe_variable("totexpcq").imputed_set is None


def test_describe_variable_names_impnum_when_versions_are_rows(
    meta_sidecar_dir, meta_data_dir
):
    docs = small_set_docs()
    mtbi = docs["ce-interview-mtbi.odcs.yaml"]["schema"][0]
    mtbi["customProperties"][1]["value"].append("impnum")
    mtbi["properties"].append(
        {
            "name": "impnum",
            "customProperties": [{"property": "ce:role", "value": "identifier"}],
        }
    )
    cost = next(c for c in mtbi["properties"] if c["name"] == "cost")
    cost["customProperties"].append(
        {"property": "ce:imputed", "value": {"multiple": True, "iterations": 5}}
    )
    service = make_service(write_set(meta_sidecar_dir, docs), meta_data_dir)
    imputed = service.describe_variable("cost").imputed_set
    assert (imputed.version_column, imputed.versions) == ("impnum", [])


def test_describe_variable_finds_a_code_by_its_label(meta_service):
    detail = meta_service.describe_variable("ucc", code_query="dining")
    assert [(c.value, c.label) for c in detail.codes] == [
        ("790410", "Dining out at restaurants")
    ]
    assert (detail.codes_total, detail.codes_matched) == (3, 1)
    by_value = meta_service.describe_variable("ucc", code_query="7902")
    assert [c.value for c in by_value.codes] == ["790210"]
    assert meta_service.describe_variable("ucc", code_limit=2).codes_total == 3
    assert len(meta_service.describe_variable("ucc", code_limit=2).codes) == 2


def test_describe_variable_reports_review_state(meta_service):
    detail = meta_service.describe_variable("fsalaryx")
    assert detail.sidecar.needs_review is True
    assert detail.sidecar.unverified == ["ce:unit"]
    assert "Under review: unit under review, needs-review." in detail.summary
    assert meta_service.describe_variable("mystery").role is None


def test_describe_variable_hides_record_level_values(meta_service):
    profile = meta_service.describe_variable("newid").profile
    assert (profile.min, profile.max) == (None, None)
    assert profile.distinct_count == 11
    income = meta_service.describe_variable("fincbtax").profile
    assert (income.min, income.max) == ("-100.0", "900000.0")


def test_describe_variable_without_a_profile_still_answers(meta_service):
    detail = meta_service.describe_variable("state")
    assert detail.profile is None
    assert detail.data_type == "string"


def test_describe_variable_refuses_an_unknown_name(meta_service):
    with pytest.raises(UnknownVariable) as exc:
        meta_service.describe_variable("regionn")
    assert exc.value.suggestions == ["region"]


# ── list_geographies ─────────────────────────────────────────────────────────


def test_list_geographies_counts_interviews_over_the_year_set(meta_service):
    listing = meta_service.list_geographies("region")
    assert (listing.table, listing.column, listing.support) == (
        "fmli",
        "region",
        "reliable",
    )
    counts = {e.code: e.interviews for e in listing.entries}
    # the four rows of the 2025 `1` file are not counted
    assert counts == {"1": 3, "2": 1, "3": 2, "4": 3, "C": 1}
    assert listing.unlisted_interviews == 0
    assert [e.label for e in listing.entries][0] == "Northeast"


def test_list_geographies_counts_blanks_apart(meta_service):
    listing = meta_service.list_geographies("state")
    assert listing.support == "limited"
    assert {e.code: e.interviews for e in listing.entries} == {"01": 3, "06": 5}
    assert listing.unlisted_interviews == 2
    assert "2 interviews carry no listed code" in listing.summary


def test_list_geographies_for_another_year(meta_service):
    listing = meta_service.list_geographies("region", 2024)
    assert {e.code: e.interviews for e in listing.entries} == {
        "1": 2,
        "2": 1,
        "3": 0,
        "4": 0,
        "C": 1,
    }


def test_list_geographies_says_when_no_column_carries_the_level(meta_service):
    for level, support in (("division", "reliable"), ("psu", "unsupported")):
        listing = meta_service.list_geographies(level)
        assert (listing.table, listing.column, listing.entries) == (None, None, [])
        assert listing.support == support
        assert "No sidecar column" in listing.notices[0].text


def test_list_geographies_without_data_gives_codes_and_no_counts(
    meta_sidecar_dir, tmp_path
):
    service = make_service(meta_sidecar_dir, tmp_path / "nothing")
    listing = service.list_geographies("region")
    assert [e.code for e in listing.entries] == ["1", "2", "3", "4", "C"]
    assert all(e.interviews is None for e in listing.entries)
    assert listing.unlisted_interviews is None
    assert any("No fmli rows are loaded" in t for t in _build(listing.notices))


def test_list_geographies_says_parents_are_not_built(meta_service):
    built = _build(meta_service.list_geographies("region").notices)
    assert any("ref/geo_crosswalk" in t for t in built)


# ── notices, dictionary, caching ─────────────────────────────────────────────


def test_a_table_level_failure_goes_on_answers_about_that_table(
    meta_sidecar_dir, meta_data_dir
):
    docs = small_set_docs()
    broken = copy.deepcopy(docs)
    flag = next(
        c
        for c in broken[f"{FMLI}.odcs.yaml"]["schema"][0]["properties"]
        if c["name"] == "fincbt_x"
    )
    flag["customProperties"][2]["value"] = "no_such_column"
    service = make_service(write_set(meta_sidecar_dir, broken), meta_data_dir)
    assert [n.scope for n in service.describe_table("fmli").notices] == ["table"]
    assert service.describe_table("mtbi").notices == []
    assert service.describe_survey().notices == []
    assert [n.table for n in service.describe_variable("region").notices][0] == "fmli"


def test_a_set_level_failure_goes_on_every_answer(meta_sidecar_dir, meta_data_dir):
    docs = small_set_docs()
    docs[MASTER_FILE]["customProperties"][0]["value"]["version"] = "9.9.9"
    service = make_service(write_set(meta_sidecar_dir, docs), meta_data_dir)
    assert service.report.set_failures
    for answer in (
        service.describe_survey(),
        service.list_datasets(),
        service.describe_table("mtbi"),
        service.search_variables("region"),
        service.describe_variable("region"),
        service.list_geographies("region"),
    ):
        assert answer.notices[0].scope == "set"


def test_dictionary_lists_the_release_by_table(meta_service):
    listing = meta_service.dictionary()
    assert list(listing) == ["fmli", "mtbi"]
    assert [m.name for m in listing["mtbi"]] == [
        "newid",
        "ucc",
        "cost",
        "ref_yr",
        "ref_mo",
    ]
    with pytest.raises(CeMetaError):
        meta_service.dictionary(2019)


def test_answers_are_kept_per_argument_set(meta_service):
    first = meta_service.describe_variable("region")
    assert meta_service.describe_variable("region") is first
    assert meta_service.describe_variable(" region ") is first
    assert meta_service.describe_variable("region", table="fmli") is not first
    assert meta_service.list_geographies("region") is meta_service.list_geographies(
        "region"
    )


def test_no_answer_holds_an_identifier_value(meta_service):
    ids = {"10", "11"}  # newid values in the fixture that are no code or count
    answers = [
        meta_service.describe_variable("newid"),
        meta_service.describe_variable("cuid"),
        meta_service.search_variables("newid"),
        meta_service.list_datasets(),
        meta_service.describe_table("fmli"),
    ]
    for answer in answers:
        dumped = answer.model_dump(mode="json", exclude={"summary"})
        text = str(dumped.get("profile", "")) + str(dumped.get("codes", ""))
        assert not any(f"'{i}'" in text for i in ids)


def test_describe_survey_says_what_is_not_held(meta_service):
    facts = meta_service.describe_survey()
    assert any("Diary Survey" in line for line in facts.not_held)
    assert any("published tables" in line for line in facts.not_held)
    assert "see not_held" in facts.summary


def test_a_quantity_dimension_is_also_usable_as_a_measure():
    from ce_meta.sidecars import Column
    from ce_meta.usage import usable_as

    def col(**facts):
        return Column("c", None, None, None, facts, (), ())

    size = col(**{"ce:role": "dimension", "ce:scale": "ratio", "ce:unit": "count"})
    assert usable_as(size) == ["measure", "grouping", "filter"]
    coded = col(**{"ce:role": "dimension", "ce:scale": "nominal"})
    assert usable_as(coded) == ["grouping", "filter"]
    no_unit = col(**{"ce:role": "dimension", "ce:scale": "ratio"})
    assert usable_as(no_unit) == ["grouping", "filter"]
