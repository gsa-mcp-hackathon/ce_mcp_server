"""Shared fixtures for loader tests: small synthetic tables and file trees."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pytest

from ce_ingest.filename import parse_filename
from ce_ingest.models import SourceFile

DATA = Path(__file__).parent / "data"


@pytest.fixture
def make_src(tmp_path: Path) -> Callable[[str], SourceFile]:
    """`make_src("intrvw25/fmli251x")` -> a SourceFile for that (empty) path."""

    def _make(rel: str) -> SourceFile:
        path = tmp_path / "pumd" / f"{rel}.sas7bdat"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return parse_filename(path)

    return _make


@pytest.fixture
def tiny_fmli() -> pa.Table:
    return pa.table(
        {
            "newid": pa.array(["05666064", "05666074", "05666094", "04917823"]),
            "cuid": pa.array([566606.0, 566607.0, 566609.0, 491782.0]),
            "qintrvmo": pa.array(["01", "02", "03", None]),
            "finlwt21": pa.array([14158.071, 35799.738, None, 7698.859]),
            "state": pa.array(["01", "06", "", "12"]),  # leading zero, blank
        }
    )


@pytest.fixture
def tiny_mtbi() -> pa.Table:
    """An item table: no cuid column."""
    return pa.table(
        {
            "newid": pa.array(["05666064", "05666064", "04917823"]),
            "ucc": pa.array(["010120", "980000", "010120"]),
            "cost": pa.array([12.5, None, 3.0]),
        }
    )


@pytest.fixture
def tiny_fmld() -> pa.Table:
    return pa.table(
        {
            "newid": pa.array(["06234661", "06234671"]),
            "cuid": pa.array([623466.0, 623467.0]),
            "fincbefx": pa.array([52000.0, None]),
        }
    )


@pytest.fixture
def sas_fixture(make_src: Callable[[str], SourceFile]) -> SourceFile:
    """The real `diary25/dtbd254.sas7bdat` (smallest 2025 file), checked in."""
    src = make_src("diary25/dtbd254")
    shutil.copyfile(DATA / "dtbd254.sas7bdat", src.path)
    return src


@pytest.fixture
def landing_tree(tmp_path: Path) -> Path:
    """A landing dir with in-scope files plus things that must be ignored."""
    root = tmp_path / "landing"
    files = [
        "pumd/intrvw25/fmli251x.sas7bdat",
        "pumd/intrvw25/fmli252.sas7bdat",
        "pumd/intrvw25/fmli261.sas7bdat",
        "pumd/intrvw25/mtbi251x.sas7bdat",
        "pumd/intrvw25/expn25/apa25.sas7bdat",  # EXPN -> expn_apa
        "pumd/intrvw25/expn25/rnt24.sas7bdat",  # EXPN, year mismatch -> reject
        "pumd/intrvw25/para25/fpar2425.sas7bdat",  # paradata -> ignored
        "pumd/intrvw25/notes.csv",  # not SAS -> ignored
        "pumd/diary25/fmld254.sas7bdat",
        "pumd/diary25/dtbd251.sas7bdat",
    ]
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    return root


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    """A temp output directory for ParquetWriter."""
    path = tmp_path / "out"
    path.mkdir()
    return path


@pytest.fixture
def tiny_expn() -> pa.Table:
    """An EXPN table covering the five quarters of release 2025."""
    return pa.table(
        {
            "newid": [
                "05666064",
                "05790012",
                "05801234",
                "05812345",
                "05823456",
                "05666064",
            ],
            "qyear": ["20251", "20252", "20253", "20254", "20261", "20251"],
            "cost": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )
