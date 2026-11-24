import duckdb
import pyarrow as pa

from ce_store.sets import survey_year_filter, survey_year_set


def test_interview_set():
    s = survey_year_set("intrvw", 2025)
    assert (2026, "1") in s and (2025, "1") not in s
    assert s == [(2025, "1x"), (2025, "2"), (2025, "3"), (2025, "4"), (2026, "1")]


def test_diary_set():
    assert survey_year_set("diary", 2025) == [(2025, q) for q in "1234"]


def test_filter_selects_exactly_the_set():
    parts = pa.table(
        {
            "survey_year": pa.array(
                [2024, 2025, 2025, 2025, 2025, 2025, 2026, 2026], pa.int32()
            ),
            "quarter_code": ["4", "1x", "1", "2", "3", "4", "1", "2"],
        }
    )
    con = duckdb.connect()
    con.register("parts", parts)
    for survey in ("intrvw", "diary"):
        got = con.execute(
            f"select survey_year, quarter_code from parts "
            f"where {survey_year_filter(survey, 2025)} order by 1, 2"
        ).fetchall()
        assert got == sorted(survey_year_set(survey, 2025))
