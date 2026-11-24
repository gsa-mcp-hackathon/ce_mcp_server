"""The answer checkers: figures, cautions, reasons and questions."""

from ce_eval.answer import (
    apply_judgments,
    check_answer,
    classify_figures,
    messages_of,
    numbers,
    result_values,
    said,
)

REPLY = """Average spending by region
  Computed: the mean of total expenditures (totexppq, totexpcq) by region,
  weighted, per consumer unit per year.
  Result:
    region Northeast: $88,010 (standard error $1,732)
    region South: $74,526 (standard error $2,231)
  Caution: region code C ("Combined") is not a region; it is reported
  separately.
"""
CAUTION = 'region code C ("Combined") is not a region; it is reported separately.'
OUTCOME = {
    "nodes": [
        {
            "id": "n1",
            "verdict": "warn",
            "messages": [{"level": "warn", "rule": "region_code_c", "text": CAUTION}],
            "result": {
                "groups": [
                    {
                        "group": {"region": "1"},
                        "value": 88010.4,
                        "standard_error": 1731.9,
                        "interviews": 2900,
                        "units": 1720,
                    },
                    {
                        "group": {"region": "3"},
                        "value": 74526.2,
                        "standard_error": 2231.0,
                        "interviews": 5400,
                        "units": 3222,
                    },
                ]
            },
        }
    ]
}
REQUEST = {"answers": [{"asks": "x", "node": "n1"}]}


def _check(answer, **kw):
    kw.setdefault("outcomes", [(REQUEST, OUTCOME)])
    kw.setdefault("questions", [])
    kw.setdefault("reply_texts", [REPLY])
    kw.setdefault(
        "context_texts", ["Average spending by region in 2025, for those 65 or older"]
    )
    return check_answer(answer, **kw)


def test_numbers_are_read_with_their_marks():
    found = {
        n.raw: n
        for n in numbers("$88,010, 70.2%, 0.702, 1.2 million, $88k, 1,720, in 2025")
    }
    assert found["$88,010"].value == 88010 and found["$88,010"].currency
    assert found["70.2%"].percent and found["70.2%"].decimals == 1
    assert found["1.2 million"].value == 1_200_000 and found["1.2 million"].scaled
    assert found["$88k"].value == 88_000
    assert found["2025"].is_year and not found["2025"].is_figure


def test_identifiers_and_small_whole_numbers_are_not_figures():
    text = "fincbtx1 in req-3fa9c2d81b7e, 5 versions, 44 weights, 65 or older, step 2."
    assert [n.raw for n in numbers(text) if n.is_figure] == []
    assert [n.raw for n in numbers("item 210110 and 0.5") if n.is_figure] == [
        "210110",
        "0.5",
    ]


def test_a_figure_from_the_reply_is_faithful_exact_or_rounded():
    figures = classify_figures(
        "About $88,010, or roughly $88,000; the South is $74.5k.",
        result_values([OUTCOME]),
        [],
    )
    assert figures.found == [
        ("$88,010", "reply"),
        ("$88,000", "rounded"),
        ("$74.5k", "rounded"),
    ]
    assert figures.unsupported == []


def test_a_proportion_may_be_said_as_a_percentage():
    figures = classify_figures("That is 70.2% of income.", [0.70213], [])
    assert figures.found == [("70.2%", "reply")]


def test_an_error_given_in_percentage_points_is_the_proportions_error():
    reference = [0.70213, 0.01874, 0.731, 0.0061]
    table = classify_figures("Northeast: 70.2% (1.9)", reference, [])
    assert table.found == [("70.2%", "reply"), ("1.9", "reply")]
    prose = classify_figures(
        "73.1%, standard error 0.6 percentage points", reference, []
    )
    assert prose.unsupported == []
    assert classify_figures("an error of 4.4 points", reference, []).unsupported == [
        "4.4"
    ]


def test_an_invented_or_worked_out_figure_is_unsupported():
    check = _check(
        "Northeast $88,010, South $74,526, a gap of $13,484. Nationally $72,967."
    )
    assert check.figures.unsupported == ["$13,484", "$72,967"]
    assert not check.faithful_numbers and check.has_result_figure


def test_counts_in_the_outcome_and_numbers_in_the_conversation_are_not_invented():
    check = _check(
        "From 1,720 units (2,900 interviews), for those 65 or older in 2025."
    )
    assert check.faithful_numbers
    check = _check("See item code 210110.", context_texts=["ucc 210110 is rent"])
    assert (
        check.figures.found == [("210110", "context")] and not check.has_result_figure
    )


def test_rounding_that_loses_the_figure_is_not_accepted():
    assert classify_figures("$90,000", [88010.0], []).unsupported == ["$90,000"]
    assert classify_figures("$2,000", [1731.9], []).unsupported == ["$2,000"]


def test_said_word_for_word_in_other_words_or_not():
    assert (
        said(
            CAUTION,
            "Note: Region code C (Combined) is NOT a region;\n"
            "it is reported separately!",
        )
        == "verbatim"
    )
    assert (
        said(CAUTION, "The Combined code C is separate, reported apart, not a region.")
        == "paraphrase"
    )
    assert said(CAUTION, "Here are the numbers you wanted.") == "missing"


def test_cautions_reasons_and_questions():
    kept = _check(REPLY)
    assert kept.cautions == {CAUTION: "verbatim"}
    dropped = _check("Northeast $88,010 and South $74,526.")
    assert dropped.cautions == {CAUTION: "missing"}
    blocked = {
        "nodes": [
            {
                "id": "n1",
                "verdict": "block",
                "messages": [
                    {
                        "level": "block",
                        "rule": "expn",
                        "text": "No estimation method is defined yet.",
                    },
                    {
                        "level": "warn",
                        "rule": "w",
                        "text": "A warning the reply does not show.",
                    },
                ],
            }
        ]
    }
    check = _check(
        "I cannot compute that: no estimation method is defined yet.",
        outcomes=[(REQUEST, blocked)],
        reply_texts=[],
    )
    assert check.reason_given and check.cautions == {} and not check.has_result_figure
    assert messages_of(REQUEST, blocked, "warn") == []
    question = "As released, or adjusted to match the published figures?"
    asked = _check(question, outcomes=[], questions=[question], reply_texts=[])
    assert asked.question_asked
    assert not _check(
        "It was $61,300.", outcomes=[], questions=[question]
    ).question_asked


def test_required_mentions_and_judgments():
    check = _check(
        "That comes from the Diary Survey.",
        outcomes=[],
        mentions=[["Diary"], ["published", "tables"]],
    )
    assert check.mentions_missing == [["published", "tables"]]
    para = _check("The Combined code C is separate, reported apart, not a region.")
    assert para.cautions[CAUTION] == "paraphrase" and para.kept(para.cautions) == 1
    assert para.kept(para.cautions, strict=True) == 0
    apply_judgments(para.cautions, {CAUTION: None})  # no verdict: unchanged
    assert para.cautions[CAUTION] == "paraphrase"
    apply_judgments(para.cautions, {CAUTION: False})
    assert para.cautions[CAUTION] == "missing"
    apply_judgments(para.cautions, {CAUTION: True})  # a judge may find it after all
    assert para.cautions[CAUTION] == "paraphrase"


def test_large_amounts_are_read_with_their_scale():
    figures = classify_figures(
        "About $12.8 trillion, give or take $176 billion.",
        [12_797_053_456_310.0, 176_300_000_000.0],
        [],
    )
    assert [kind for _, kind in figures.found] == ["rounded", "rounded"]
