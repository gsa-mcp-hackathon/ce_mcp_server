"""Normalize (`ce_capture.normalize`): names and codes, never method."""

from ce_capture.normalize import code_for, normalize
from tests.capture.conftest import node


def _fixes(fixes):
    return [(f["field"], f["from"], f["to"], f["rule"]) for f in fixes]


def test_bls_spelling_and_table_ids_become_stored_names(ctx):
    n = node(
        measure={"table": "FMLI", "column": "TOTEXPCQ"},
        group_by=[{"table": "ce-interview-fmli", "column": "Region"}],
    )
    nodes, fixes, flags = normalize([n], [], ctx)
    assert nodes[0]["measure"] == {"table": "fmli", "column": "totexpcq"}
    assert nodes[0]["group_by"] == [{"table": "fmli", "column": "region"}]
    assert _fixes(fixes) == [
        ("measure.table", "FMLI", "fmli", "table_name"),
        ("measure.column", "TOTEXPCQ", "totexpcq", "column_name"),
        ("group_by.0.table", "ce-interview-fmli", "fmli", "table_name"),
        ("group_by.0.column", "Region", "region", "column_name"),
    ]
    assert flags == []
    assert n["measure"]["table"] == "FMLI"  # the input is not changed


def test_an_unknown_name_is_left_for_the_check(ctx):
    n = node(measure={"table": "fmli", "column": "totexpq"})
    nodes, fixes, _ = normalize([n], [], ctx)
    assert nodes[0]["measure"]["column"] == "totexpq" and fixes == []


def test_labels_and_typed_values_become_codes(ctx):
    n = node(
        filters=[
            {
                "table": "fmli",
                "column": "region",
                "comparison": "eq",
                "value": "northeast",
            },
            {
                "table": "fmli",
                "column": "region",
                "comparison": "in",
                "value": [3, "West"],
            },
            {"table": "fmli", "column": "state", "comparison": "eq", "value": "06"},
        ]
    )
    nodes, fixes, _ = normalize([n], [], ctx)
    assert [f["value"] for f in nodes[0]["filters"]] == ["1", ["3", "4"], "06"]
    assert [f["rule"] for f in fixes] == [
        "filter_label",
        "filter_value_type",
        "filter_label",
    ]


def test_code_for():
    codes = (
        ("1", "Northeast"),
        ("2", "Midwest"),
        ("05", "Same label"),
        ("06", "Same label"),
    )
    assert code_for("1", codes) == ("1", "")
    assert code_for(2, codes) == ("2", "filter_value_type")
    assert code_for(2.0, codes) == ("2", "filter_value_type")
    assert code_for(" MIDWEST ", codes) == ("2", "filter_label")
    assert code_for("Same label", codes) == (None, "")  # two codes carry it
    assert code_for("nowhere", codes) == (None, "")


def test_a_comparison_of_numbers_is_left_alone(ctx):
    n = node(
        filters=[
            {"table": "fmli", "column": "region", "comparison": "gt", "value": "North"}
        ]
    )
    nodes, fixes, _ = normalize([n], [], ctx)
    assert nodes[0]["filters"][0]["value"] == "North" and fixes == []


def test_the_years_in_the_users_words_win(ctx):
    n = node(period={"text": "for 2024 and 2025", "kind": "year", "values": [2025]})
    nodes, fixes, _ = normalize([n], [], ctx)
    assert nodes[0]["period"]["values"] == [2024, 2025]
    assert fixes[0]["rule"] == "period"
    q = node(period={"text": "in Q2 2025", "kind": "quarter", "values": []})
    assert normalize([q], [], ctx)[0][0]["period"]["values"] == ["2025Q2"]
    stated = node(period={"text": "in 2025", "kind": "not_stated", "values": []})
    nodes, fixes, _ = normalize([stated], [], ctx)
    assert nodes[0]["period"] == {"text": "in 2025", "kind": "year", "values": [2025]}


def test_a_statistic_term_that_differs_is_a_flag_not_a_change(ctx):
    n = node(statistic="total")
    answers = [{"asks": "what's the average spending", "node": "n1"}]
    nodes, _, flags = normalize([n], answers, ctx)
    assert nodes[0]["statistic"] == "total"
    assert flags == ["statistic_term_differs: n1 is total; the words say 'average'"]
    ok = [{"asks": "average total spending", "node": "n1"}]
    assert normalize([node()], ok, ctx)[2] == []
