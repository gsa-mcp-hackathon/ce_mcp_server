"""Identify PUMD SAS files from their folder and name.

Two layouts are in scope:

- quarterly files: `pumd/intrvwYY/<table><yy><q>[x].sas7bdat` and
  `pumd/diaryYY/<table><yy><q>.sas7bdat`;
- detailed expenditure (EXPN) files: `pumd/intrvwYY/expnYY/<code><yy>.sas7bdat`,
  one file spanning the release's five quarters.

Paradata (`paraYY/`) is never read.
"""

import logging
import re
from pathlib import Path

from ce_ingest.errors import FilenameError
from ce_ingest.models import TABLES_BY_SURVEY, SourceFile, Survey

log = logging.getLogger(__name__)

_FILE_RE = re.compile(
    r"^(?P<file>[a-z]{4})(?P<yy>\d{2})(?P<q>[1-4])(?P<x>x?)\.sas7bdat$", re.IGNORECASE
)
_EXPN_FILE_RE = re.compile(
    r"^(?P<code>[a-z]{3})(?P<yy>\d{2})\.sas7bdat$", re.IGNORECASE
)
_FOLDER_RE = re.compile(r"^(?P<survey>intrvw|diary)(?P<yy>\d{2})$", re.IGNORECASE)
_EXPN_FOLDER_RE = re.compile(r"^expn(?P<yy>\d{2})$", re.IGNORECASE)


def _parse_expn(path: Path) -> SourceFile:
    folder = _EXPN_FOLDER_RE.match(path.parent.name)
    survey_folder = _FOLDER_RE.match(path.parent.parent.name)
    assert folder is not None
    if survey_folder is None or survey_folder["survey"].lower() != "intrvw":
        raise FilenameError(f"{path}: an expnYY folder must sit inside intrvwYY")
    m = _EXPN_FILE_RE.match(path.name)
    if m is None:
        raise FilenameError(f"{path}: EXPN name doesn't match <code><yy>.sas7bdat")
    if m["yy"] != folder["yy"]:
        raise FilenameError(
            f"{path}: file year {m['yy']} doesn't match folder {path.parent.name}"
        )
    return SourceFile(
        path=path,
        survey="intrvw",
        table=f"expn_{m['code'].lower()}",
        survey_year=2000 + int(m["yy"]),
        quarter=None,
        quarter_code=None,
        kind="multi_quarter",
    )


def parse_filename(path: Path) -> SourceFile:
    """Read survey, table, year and quarter from the folder and file name."""
    if _EXPN_FOLDER_RE.match(path.parent.name):
        return _parse_expn(path)

    folder = _FOLDER_RE.match(path.parent.name)
    if folder is None:
        raise FilenameError(
            f"{path}: folder {path.parent.name!r} is not intrvwYY, diaryYY or expnYY"
        )
    survey: Survey = folder["survey"].lower()  # type: ignore[assignment]

    m = _FILE_RE.match(path.name)
    if m is None:
        raise FilenameError(
            f"{path}: name doesn't match <table><yy><quarter 1-4>[x].sas7bdat"
        )
    table = m["file"].lower()
    if table not in TABLES_BY_SURVEY[survey]:
        allowed = ", ".join(sorted(TABLES_BY_SURVEY[survey]))
        raise FilenameError(
            f"{path}: table {table!r} is not an in-scope {survey} table ({allowed})"
        )
    x = m["x"].lower()
    if x and survey == "diary":
        raise FilenameError(f"{path}: Diary files have no 'x' quarter code")

    return SourceFile(
        path=path,
        survey=survey,
        table=table,
        survey_year=2000 + int(m["yy"]),
        quarter=int(m["q"]),
        quarter_code=m["q"] + x,
    )


def _sas_files(folder: Path) -> list[Path]:
    return [
        p
        for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() == ".sas7bdat"
    ]


def _candidates(landing_dir: Path, survey: Survey | None) -> list[Path]:
    """SAS files in `pumd/intrvwYY/`, `pumd/diaryYY/` and `pumd/intrvwYY/expnYY/`."""
    paths: list[Path] = []
    pumd = landing_dir / "pumd"
    if not pumd.is_dir():
        return paths
    for folder in sorted(pumd.iterdir()):
        m = _FOLDER_RE.match(folder.name)
        if not folder.is_dir() or m is None:
            continue
        if survey is not None and m["survey"].lower() != survey:
            continue
        paths.extend(_sas_files(folder))
        # One level further, into expnYY/ only: paraYY/ is never read.
        for sub in sorted(folder.iterdir()):
            if sub.is_dir() and _EXPN_FOLDER_RE.match(sub.name):
                paths.extend(_sas_files(sub))
    return paths


def _sort_key(src: SourceFile) -> tuple[str, str, int, str]:
    return (src.survey, src.table, src.survey_year, src.quarter_code or "")


def discover_files(landing_dir: Path, survey: Survey | None = None) -> list[SourceFile]:
    """Every in-scope SAS file, sorted by survey, table, year and quarter code.

    Files that don't parse are logged and skipped; see `discover_rejects`.
    """
    found: list[SourceFile] = []
    for path in _candidates(landing_dir, survey):
        try:
            found.append(parse_filename(path))
        except FilenameError as exc:
            log.info("skipping %s", exc)
    return sorted(found, key=_sort_key)


def discover_rejects(
    landing_dir: Path, survey: Survey | None = None
) -> list[tuple[Path, str]]:
    """SAS files in the landing folders that `discover_files` skipped, with why."""
    rejects: list[tuple[Path, str]] = []
    for path in _candidates(landing_dir, survey):
        try:
            parse_filename(path)
        except FilenameError as exc:
            rejects.append((path, str(exc)))
    return rejects
