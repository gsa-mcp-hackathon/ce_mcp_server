"""Finding the user's words (`ce_capture.text`) and reading periods."""

import pytest

from ce_capture.periods import period_years, quarters_in, value_year, years_in
from ce_capture.text import has_phrase, locate, words

TEXT = "For 2025, as released, what's average spending per household by region?"


@pytest.mark.parametrize(
    ("needle", "found"),
    [
        ("as released", "as released"),
        ("AS  RELEASED", "as released"),
        ("as-released", "as released"),
        ("whats average spending", "what's average spending"),
        ("by region?", "by region"),
        ("For 2025,", "For 2025"),
    ],
)
def test_locate_ignores_case_spacing_and_punctuation(needle, found):
    start, end = locate(needle, TEXT)
    assert TEXT[start:end] == found


def test_locate_takes_the_first_occurrence_and_fails_cleanly():
    assert locate("a", "b a a") == (2, 3)
    assert locate("adjusted", TEXT) is None
    assert locate("", TEXT) is None
    assert locate("...", TEXT) is None


def test_curly_apostrophes_and_accents():
    text = "What’s the café count?"
    assert text[slice(*locate("whats the cafe count", text))] == "What’s the café count"


def test_phrases_match_whole_words():
    w = words("Unweighted, please: don't-knows in. Current rent.")
    assert w == "unweighted please dont knows in current rent"
    assert has_phrase(w, "unweighted")
    assert not has_phrase(w, "weighted")  # part of "unweighted"
    assert has_phrase(w, "don't knows")
    assert has_phrase(w, "rent") and not has_phrase(w, "curr")
    assert not has_phrase(w, "")


def test_years_and_quarters_in_words():
    assert years_in("from 2024 to 2025, and 2024 again") == [2024, 2025]
    assert years_in("in 12025 or 1975") == []
    assert quarters_in("Q2 2025 and 2025Q3, the fourth quarter of 2025") == [
        "2025Q2",
        "2025Q3",
        "2025Q4",
    ]


def test_period_years():
    assert period_years({"kind": "year", "values": [2024, 2025]}, 2025) == [2024, 2025]
    assert period_years({"kind": "quarter", "values": ["2025Q2"]}, 2025) == [2025]
    month_range = {"kind": "month_range", "values": ["2024-11", "2025-02"]}
    assert period_years(month_range, 2025) == [2024, 2025]
    assert period_years({"kind": "not_stated", "values": []}, 2025) == [2025]
    assert period_years(None, 2025) == [2025]
    assert value_year("not a year") is None
