"""A tiny dictionary workbook and a tiny stored `fmli`, for the build steps."""

from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow")
openpyxl = pytest.importorskip("openpyxl")

from ce_ingest.derive import add_partition_columns  # noqa: E402
from ce_ingest.models import ColumnMeta  # noqa: E402
from ce_ingest.registry import record_source_columns  # noqa: E402
from ce_ingest.writers import ParquetWriter  # noqa: E402
from tests.ingest.conftest import make_src, out_dir  # noqa: E402, F401

VARIABLES = [
    # survey, file, name, description, flag, first, last
    ("INTERVIEW", "FMLI", "REGION", "Census region", "REGI_N", 1984, None),
    ("INTERVIEW", "FMLI", "OLDVAR", "Dropped long ago", "", 1984, 2001),
    ("INTERVIEW", "FMLI", "RENAMED", "First meaning", "", 1984, 1999),
    ("INTERVIEW", "FMLI", "RENAMED", "Second meaning", "", 2000, None),
    ("INTERVIEW", "RNT", "RTCOMPX", "Rent paid", "", 2013, None),
    ("INTERVIEW", "VEQ", "VEQX", "A file with no stored table", "", 1990, None),
    ("DIARY", "FMLD", "WK_WRKD", "Weeks worked", "", 1990, None),
    ("DIARY", "FMLD", "REGION", "Census region", "", 1990, None),
]
CODES = [
    # survey, file, variable, value, label, first, last
    ("INTERVIEW", "FMLI", "REGION", "1", "Northeast", 1996, None),
    ("INTERVIEW", "FMLI", "REGION", "1", "Northeast", 1996, None),  # a duplicate
    ("INTERVIEW", "FMLI", "REGION", "1", "Northeast", 1980, 1981),
    ("INTERVIEW", "FMLI", "REGION", "3", "South", 1996, None),
    ("INTERVIEW", "FMLI", "REGION", "4", "West", 1996, None),
    ("INTERVIEW", "FMLI", "REGION", "C", "Combined", 2025, None),
    ("INTERVIEW", "FMLI", "DIVISION", "1", "New England", 2015, None),
    ("INTERVIEW", "FMLI", "DIVISION", "5", "South Atlantic", 2015, None),
    ("INTERVIEW", "FMLI", "DIVISION", "9", "Pacific", 2015, None),
    ("INTERVIEW", "FMLI", "STATE", "06", "California", 1984, None),
    ("INTERVIEW", "FMLI", "STATE", "25", "Massachusetts", 1984, None),
    ("INTERVIEW", "FMLI", "STATE", "33", "New Hampshire", 1984, None),
    ("INTERVIEW", "FMLI", "STATE", "72", "Puerto Rico", 1984, None),
    ("INTERVIEW", "FMLI", "PSU", "S11A", "Boston-Cambridge-Newton, MA-NH", 2015, None),
    (
        "INTERVIEW",
        "FMLI",
        "PSU",
        "S49A",
        "Los Angeles-Long Beach-Anaheim, CA",
        2015,
        None,
    ),
    ("INTERVIEW", "FMLI", "PSU", "S35A", "Washington, DC", 2015, None),
    ("INTERVIEW", "FMLI", "PSU", "A109", "An area of the old design", 1996, 2014),
    ("DIARY", "FMLD", "REGION", "1", "Northeast", 1996, None),
]


@pytest.fixture
def dictionary(tmp_path) -> Path:
    wb = openpyxl.Workbook()
    v = wb.active
    v.title = "Variables"
    v.append(
        ["Survey", "File", "Variable Name", "Variable description", "Formula ",
         "Flag name", "Section number", "Section description", "Section part",
         "First year", "First Quarter", "Last year", "Last quarter", "Comment"]
    )  # fmt: skip
    for survey, file, name, text, flag, first, last in VARIABLES:
        v.append(
            [survey, file, name, text, "", flag, 1, "A section", "", first, 1, last,
             None, ""]
        )  # fmt: skip
    c = wb.create_sheet("Codes ")  # the real sheet name has a trailing space
    c.append(
        ["Survey", "File", "Variable ", "Code value", "Code description",
         "First year", "First quarter", "Last year", "Last quarter", "Comment"]
    )  # fmt: skip
    for survey, file, variable, value, label, first, last in CODES:
        c.append([survey, file, variable, value, label, first, 1, last, None, ""])
    path = tmp_path / "dictionary.xlsx"
    wb.save(path)
    return path


# newid, region, division, state, psu
FMLI_ROWS = [
    ("00000011", "1", "1", "25", "S11A"),  # Boston, in Massachusetts
    ("00000021", "1", "1", "33", "S11A"),  # Boston, in New Hampshire
    ("00000031", "4", "9", "06", "S49A"),  # Los Angeles
    ("00000041", "4", "9", "06", ""),  # no PSU
    ("00000051", "C", "", "", ""),  # region C: left out of the check
    ("00000061", "3", "5", "", "S35A"),  # a blank state: left out
]


@pytest.fixture
def stored(out_dir, make_src):  # noqa: F811
    """A writer holding a small `fmli` and an `expn_rnt`, with source columns."""
    writer = ParquetWriter(out_dir)
    columns = ["newid", "region", "division", "state", "psu"]
    table = pa.table({c: [r[i] for r in FMLI_ROWS] for i, c in enumerate(columns)})
    src = make_src("intrvw25/fmli252")
    writer.write_partition(src, add_partition_columns(table, src))
    record_source_columns(
        writer,
        src,
        [
            ColumnMeta(
                name=c,
                original_name=c.upper(),
                sas_type="character",
                length=8,
                label=None,
                format=None,
            )
            for c in columns
        ],
    )
    rnt = make_src("intrvw25/expn25/rnt25")
    rnt = rnt.model_copy(
        update={"quarter": 2, "quarter_code": "2", "kind": "quarterly"}
    )
    writer.write_partition(
        rnt, add_partition_columns(pa.table({"newid": ["00000011"]}), rnt)
    )
    return writer
