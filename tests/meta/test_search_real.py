"""`search_variables` on the real sidecar set: the queries a model typed in the
first evaluation (eval/baseline) and got nothing for, though the column was
there. Each is a way a person would put it, longer than any column's own words.
"""

from pathlib import Path

import pytest

from ce_mcp_server.service import build_service
from ce_meta.config import MetaSettings

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def service():
    settings = MetaSettings(sidecar_dir=ROOT / "sidecars", data_dir=ROOT / "no-data")
    return build_service(settings)


def top(service, query, n=2, **kw) -> list[str]:
    found = service.search_variables(query, **kw)
    return [f"{m.table}.{m.name}" for m in found.matches[:n]]


@pytest.mark.parametrize(
    "query",
    [
        "Toyota vehicle make",
        "vehicle make manufacturer brand",
        "households that own a Toyota",
        "owned a Toyota",
    ],
)
def test_the_vehicle_make_column_is_found(service, query):
    assert "expn_ovb.make" in top(service, query)


@pytest.mark.parametrize(
    "query",
    ["full-time college student in household", "school enrollment full time"],
)
def test_the_college_enrolment_column_is_found(service, query):
    assert top(service, query, 1) == ["memi.in_coll"]


def test_a_close_match_says_what_it_lacks_and_shows_the_code(service):
    found = service.search_variables("households that own a Toyota")
    make = next(m for m in found.matches if (m.table, m.name) == ("expn_ovb", "make"))
    assert make.lacks == ["households", "own"]
    assert [c.value for c in make.matched_codes] == ["TOY"]
    assert found.summary.startswith("No column holds every word")


def test_a_long_code_list_does_not_match_everything(service):
    """mtbi.ucc has hundreds of labels; between them they hold most words. It
    must not come back for a query no single one of its codes is about."""
    for query in ("Toyota vehicle make", "school enrollment full time"):
        assert "mtbi.ucc" not in top(service, query, 5)


def test_exact_and_plain_searches_are_as_before(service):
    assert top(service, "fincbtxm", 1) == ["fmli.fincbtxm"]
    assert top(service, "income before taxes", 1) == ["fmli.fincbtxm"]
    assert top(service, "Toyota") == ["expn_lsd.lsd_make", "expn_ovb.make"]
    assert service.search_variables("brand").total == 0
    by_use = service.search_variables("college student", usable_as="filter")
    assert all("filter" in m.usable_as for m in by_use.matches)
