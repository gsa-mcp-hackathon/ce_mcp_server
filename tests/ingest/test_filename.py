from pathlib import Path

import pytest

from ce_ingest.errors import FilenameError
from ce_ingest.filename import discover_files, discover_rejects, parse_filename


@pytest.mark.parametrize(
    "rel, survey, table, year, quarter, code",
    [
        ("intrvw25/fmli251x.sas7bdat", "intrvw", "fmli", 2025, 1, "1x"),
        ("intrvw25/fmli261.sas7bdat", "intrvw", "fmli", 2026, 1, "1"),
        ("diary25/fmld254.sas7bdat", "diary", "fmld", 2025, 4, "4"),
        ("INTRVW25/MTBI252.SAS7BDAT", "intrvw", "mtbi", 2025, 2, "2"),
    ],
)
def test_parse_filename(rel, survey, table, year, quarter, code):
    src = parse_filename(Path("/land/pumd") / rel)
    assert (src.survey, src.table, src.survey_year, src.quarter, src.quarter_code) == (
        survey,
        table,
        year,
        quarter,
        code,
    )


@pytest.mark.parametrize(
    "rel",
    [
        "intrvw25/tre25.sas7bdat",
        "intrvw25/fpar2425.sas7bdat",
        "intrvw25/fmli255.sas7bdat",
        "diary25/fmld251x.sas7bdat",  # x on a Diary file
        "diary25/fmli251.sas7bdat",  # Interview table in the Diary folder
        "other25/fmli251.sas7bdat",  # unknown folder
    ],
)
def test_parse_filename_rejects(rel):
    with pytest.raises(FilenameError):
        parse_filename(Path("/land/pumd") / rel)


def test_discover_files_order_and_scope(landing_tree):
    found = [
        (s.survey, s.table, s.survey_year, s.quarter_code)
        for s in discover_files(landing_tree)
    ]
    assert found == [
        ("diary", "dtbd", 2025, "1"),
        ("diary", "fmld", 2025, "4"),
        ("intrvw", "expn_apa", 2025, None),
        ("intrvw", "fmli", 2025, "1x"),
        ("intrvw", "fmli", 2025, "2"),
        ("intrvw", "fmli", 2026, "1"),
        ("intrvw", "mtbi", 2025, "1x"),
    ]


def test_discover_files_by_survey(landing_tree):
    assert {s.survey for s in discover_files(landing_tree, "diary")} == {"diary"}


def test_discover_rejects(landing_tree):
    rejects = discover_rejects(landing_tree)
    assert [p.name for p, _ in rejects] == ["rnt24.sas7bdat"]


def test_discover_missing_landing(tmp_path):
    assert discover_files(tmp_path / "nope") == []


def test_parse_expn():
    src = parse_filename(Path("/land/pumd/intrvw25/expn25/rnt25.sas7bdat"))
    assert (src.survey, src.table, src.survey_year, src.kind) == (
        "intrvw",
        "expn_rnt",
        2025,
        "multi_quarter",
    )
    assert src.quarter is None and src.quarter_code is None
    assert src.label == "intrvw25/expn25/rnt25"


@pytest.mark.parametrize(
    "rel",
    [
        "intrvw25/expn25/rnt24.sas7bdat",  # year mismatch
        "intrvw25/expn25/rent25.sas7bdat",  # four-letter code
        "diary25/expn25/rnt25.sas7bdat",  # expn outside intrvwYY
    ],
)
def test_parse_expn_rejects(rel):
    with pytest.raises(FilenameError):
        parse_filename(Path("/land/pumd") / rel)
