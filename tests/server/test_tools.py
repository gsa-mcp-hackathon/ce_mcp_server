"""The six lookup tools, called the way a client calls them.

The contract tests run on the small fixture set. The size test and the
lookup-for-capture test run on the real sidecars under `sidecars/`, which are
part of the source; they need no loaded data, and use it only when it is there.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from ce_mcp_server import service as holder
from ce_mcp_server.app import mcp
from ce_meta.config import MetaSettings

ROOT = Path(__file__).parents[2]
TOOLS = [
    "describe_survey",
    "list_datasets",
    "describe_table",
    "search_variables",
    "describe_variable",
    "list_geographies",
]
MAX_TOKENS = 2000  # at four characters a token


async def call(tool: str, /, **arguments) -> dict:
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments)
    return result.data


def tokens(answer: dict) -> int:
    return len(json.dumps(answer)) // 4


@pytest.fixture
def small(meta_service):
    """The server answering from the fixture set."""
    holder.set_service(meta_service)
    yield meta_service
    holder.set_service(None)


@pytest.fixture(scope="module")
def real():
    """The server answering from the real sidecars."""
    settings = MetaSettings(
        sidecar_dir=ROOT / "sidecars", data_dir=ROOT / "data/parquet"
    )
    holder.set_service(holder.build_service(settings))
    yield
    holder.set_service(None)


# ── contract ─────────────────────────────────────────────────────────────────


async def test_every_tool_is_read_only_and_described():
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(TOOLS) <= set(tools)
    for tool in (tools[name] for name in TOOLS):
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert "capturing a request" in tool.description
        for name, prop in tool.input_schema["properties"].items():
            assert prop.get("description"), f"{tool.name}.{name} has no description"


async def test_inputs_are_typed():
    async with Client(mcp) as client:
        tools = {t.name: t.input_schema for t in await client.list_tools()}
    assert tools["describe_survey"]["properties"] == {}
    assert tools["describe_table"]["required"] == ["table"]
    assert tools["search_variables"]["required"] == ["query"]
    assert tools["describe_variable"]["required"] == ["name"]
    assert tools["list_geographies"]["required"] == ["level"]
    usable = tools["search_variables"]["properties"]["usable_as"]["anyOf"][0]
    assert usable["enum"] == ["measure", "grouping", "filter"]
    level = tools["list_geographies"]["properties"]["level"]
    assert level["enum"] == ["region", "division", "state", "psu"]


async def test_every_answer_has_a_summary(small):
    answers = [
        await call("describe_survey"),
        await call("list_datasets"),
        await call("describe_table", table="mtbi"),
        await call("search_variables", query="income"),
        await call("describe_variable", name="region"),
        await call("list_geographies", level="region"),
    ]
    for answer in answers:
        assert isinstance(answer["summary"], str) and answer["summary"]
        assert "error" not in answer


async def test_answers_leave_out_empty_and_default_fields(small):
    search = await call("search_variables", query="tenure")
    match = search["matches"][0]
    assert "matched_codes" not in match and "first_year" not in match
    assert "notices" not in search  # nothing to say
    detail = await call("describe_variable", name="region")
    assert "calendar_pair" not in detail and "imputed_set" not in detail
    assert detail["usable_as"] == ["grouping", "filter"]
    assert detail["profile"] == {"rows": 14, "null_share": 0.0, "distinct_count": 5,
                                 "min": "1", "max": "C"}  # fmt: skip


async def test_describe_variable_passes_its_arguments(small):
    detail = await call(
        "describe_variable", name="UCC", table="mtbi", code_query="rent"
    )
    assert [c["value"] for c in detail["codes"]] == ["210110"]
    assert (detail["codes_total"], detail["codes_matched"]) == (3, 1)


async def test_search_passes_its_arguments(small):
    found = await call(
        "search_variables", query="income", usable_as="measure", limit=3, table="fmli"
    )
    assert len(found["matches"]) == 3
    assert all("measure" in m["usable_as"] for m in found["matches"])


async def test_list_geographies_gives_the_column_and_counts(small):
    listing = await call("list_geographies", level="state", year=2025)
    assert (listing["table"], listing["column"]) == ("fmli", "state")
    assert listing["support"] == "limited"
    assert {"code": "06", "label": "California", "interviews": 5} in listing["entries"]


# ── the five error behaviors ─────────────────────────────────────────────────


async def test_an_unknown_variable_gives_the_closest_names(small):
    answer = await call("describe_variable", name="regionn")
    assert answer == {
        "error": "No variable named 'regionn'.",
        "suggestions": ["region"],
    }


async def test_an_unknown_table_gives_the_closest_tables(small):
    for answer in (
        await call("describe_table", table="mtbx"),
        await call("search_variables", query="cost", table="mtbx"),
        await call("describe_variable", name="cost", table="mtbx"),
    ):
        assert answer == {"error": "No table named 'mtbx'.", "suggestions": ["mtbi"]}


async def test_an_empty_query_is_refused_with_a_hint(small):
    answer = await call("search_variables", query="  ")
    assert answer["error"] == "The query is empty."
    assert answer["suggestions"]


async def test_a_bad_argument_is_refused_by_the_schema(small):
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="level|validation|enum"):
            await client.call_tool("list_geographies", {"level": "county"})


async def test_an_unexpected_failure_gives_no_stack_trace(small, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret detail /private/path")

    monkeypatch.setattr(small, "describe_table", boom)
    answer = await call("describe_table", table="fmli")
    assert list(answer) == ["error"]
    assert "secret" not in answer["error"] and "Traceback" not in answer["error"]


# ── privacy and size ─────────────────────────────────────────────────────────


async def test_no_tool_gives_an_identifier_value(small):
    answers = [
        await call("describe_variable", name="newid"),
        await call("describe_variable", name="cuid"),
        await call("search_variables", query="newid"),
    ]
    for answer in answers:
        assert "min" not in answer.get("profile", {})
        assert "max" not in answer.get("profile", {})
        assert "top_values" not in json.dumps(answer)


DEFAULT_CALLS = [
    ("describe_survey", {}),
    ("list_datasets", {}),
    ("describe_table", {"table": "fmli"}),
    ("describe_table", {"table": "mtbi"}),
    ("describe_table", {"table": "expn_rnt"}),
    ("search_variables", {"query": "income"}),
    ("search_variables", {"query": "rent"}),
    ("search_variables", {"query": "total", "usable_as": "measure"}),
    ("search_variables", {"query": "flag"}),
    ("describe_variable", {"name": "region"}),
    ("describe_variable", {"name": "ucc", "table": "mtbi"}),
    ("describe_variable", {"name": "ucc", "table": "mtbi", "code_query": "food"}),
    ("describe_variable", {"name": "fincbtxm"}),
    ("describe_variable", {"name": "fsal_ryx"}),
    ("describe_variable", {"name": "newid"}),
    ("list_geographies", {"level": "region"}),
    ("list_geographies", {"level": "state"}),
    ("list_geographies", {"level": "psu"}),
    ("list_geographies", {"level": "division"}),
]


@pytest.mark.parametrize(("tool", "arguments"), DEFAULT_CALLS)
async def test_no_answer_on_the_real_sidecars_is_over_the_size_cap(
    real, tool, arguments
):
    answer = await call(tool, **arguments)
    assert "error" not in answer
    assert tokens(answer) <= MAX_TOKENS, f"{tool} {arguments}: {tokens(answer)} tokens"


# ── lookup for capture ───────────────────────────────────────────────────────
#
# Ten requests a user might make, each with the lookups a capturing model would
# run and what it must be able to take from the answers: the table, the
# columns, and the codes of the request record (Plan 4).


def _has(matches: list[dict], table: str, name: str) -> dict:
    found = [m for m in matches if (m["table"], m["name"]) == (table, name)]
    assert found, f"{table}.{name} not in {[(m['table'], m['name']) for m in matches]}"
    return found[0]


async def test_capture_1_average_total_spending_by_region(real):
    found = await call(
        "search_variables", query="total expenditures", usable_as="measure"
    )
    measure = _has(found["matches"], "fmli", "totexpcq")
    assert measure["unit"] == "usd_quarterly"
    detail = await call("describe_variable", name="totexpcq", table="fmli")
    assert detail["calendar_pair"] == "totexppq"
    place = await call("list_geographies", level="region")
    assert (place["table"], place["column"], place["support"]) == (
        "fmli",
        "region",
        "reliable",
    )


async def test_capture_2_income_before_taxes_by_housing_tenure(real):
    found = await call(
        "search_variables", query="income before taxes", usable_as="measure"
    )
    measure = _has(found["matches"], "fmli", "fincbtxm")
    assert measure["unit"] == "usd_annual"
    grouping = await call("search_variables", query="tenure", usable_as="grouping")
    _has(grouping["matches"], "fmli", "cutenure")
    detail = await call("describe_variable", name="fincbtxm")
    assert detail["imputed_set"]["base"] == "fincbtax"
    assert len(detail["imputed_set"]["versions"]) == 5


async def test_capture_3_households_in_the_northeast(real):
    detail = await call("describe_variable", name="region", code_query="northeast")
    assert detail["codes"] == [{"value": "1", "label": "Northeast"}]
    assert "filter" in detail["usable_as"]


async def test_capture_4_average_rent_from_the_monthly_table(real):
    tables = await call("list_datasets")
    assert "mtbi" in [d["table"] for d in tables["datasets"]]
    table = await call("describe_table", table="mtbi")
    assert table["weight_route"]["parent"] == "fmli"
    assert table["weight_route"]["child_rows_optional"] is True
    measure = await call(
        "search_variables", query="cost", table="mtbi", usable_as="measure"
    )
    assert _has(measure["matches"], "mtbi", "cost")["unit"] == "usd_monthly"
    codes = await call(
        "describe_variable", name="ucc", table="mtbi", code_query="rental charge"
    )
    assert [c["value"] for c in codes["codes"]] == ["210110"]


async def test_capture_5_spending_in_california(real):
    place = await call("list_geographies", level="state")
    assert (place["column"], place["support"]) == ("state", "limited")
    assert "California" in [e["label"] for e in place["entries"] if e["code"] == "06"]
    survey = await call("describe_survey")
    assert any("state" in c.lower() for c in survey["constraints"])


async def test_capture_6_households_with_someone_65_or_older(real):
    table = await call("describe_table", table="memi")
    assert table["row_represents"] == "member_interview"
    assert table["row_key"] == ["newid", "membno"]
    found = await call("search_variables", query="age", table="memi")
    age = _has(found["matches"], "memi", "age")
    # the sidecar has not settled this column, and the answer must say so
    assert age["usable_as"] == [] and "role not assigned" in age["review"]


async def test_capture_7_wages_from_the_income_table(real):
    measure = await call(
        "search_variables", query="value", table="itbi", usable_as="measure"
    )
    value = _has(measure["matches"], "itbi", "value")
    assert "unit under review" in value["review"]
    codes = await call(
        "describe_variable", name="ucc", table="itbi", code_query="wages"
    )
    assert {"value": "900000", "label": "Wages and salaries"} in codes["codes"]


async def test_capture_8_a_detailed_expenditure_table(real):
    tables = await call("list_datasets")
    rnt = next(d for d in tables["datasets"] if d["table"] == "expn_rnt")
    assert rnt["title"].startswith("Rented Living Quarters")
    assert rnt["row_represents"] == "expn_record"
    table = await call("describe_table", table="expn_rnt")
    assert table["row_key"] == ["newid", "seqno", "alcno"]
    assert table["weight_route"]["child_rows_optional"] is True
    found = await call(
        "search_variables", query="rent", table="expn_rnt", usable_as="measure"
    )
    assert found["total"] >= 1


async def test_capture_9_vehicle_make_as_a_grouping(real):
    found = await call(
        "search_variables", query="make", table="expn_ovb", usable_as="grouping"
    )
    _has(found["matches"], "expn_ovb", "make")
    detail = await call(
        "describe_variable", name="make", table="expn_ovb", code_limit=5
    )
    assert len(detail["codes"]) == 5 and detail["codes_total"] > 5


async def test_capture_10_metropolitan_areas_are_not_supported(real):
    place = await call("list_geographies", level="psu")
    assert (place["column"], place["support"]) == ("psu", "unsupported")
    assert len(place["entries"]) == 23
    size = await call(
        "search_variables", query="size", table="fmli", usable_as="grouping"
    )
    _has(size["matches"], "fmli", "fam_size")


# ── what the server may import ───────────────────────────────────────────────


def test_the_server_loads_neither_the_loader_nor_the_generator():
    code = (
        "import sys, ce_mcp_server.app, ce_mcp_server.service, ce_meta.service\n"
        "banned = ('ce_ingest', 'ce_sidecars', 'pandas', 'pyarrow')\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in banned "
        "or m.startswith('ce_meta.build'))\n"
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert out.stdout.strip() == ""


# ── starting with a sidecar set that fails its checks ────────────────────────


def test_the_server_starts_with_a_failing_set_and_logs_each_failure(
    meta_sidecar_dir, meta_data_dir, caplog
):
    from ce_meta.sidecars import MASTER_FILE
    from tests.conftest import small_set_docs, write_set

    docs = small_set_docs()
    docs[MASTER_FILE]["customProperties"][0]["value"]["version"] = "9.9.9"
    write_set(meta_sidecar_dir, docs)
    settings = MetaSettings(sidecar_dir=meta_sidecar_dir, data_dir=meta_data_dir)
    with caplog.at_level("WARNING", logger="ce_mcp_server.service"):
        service = holder.build_service(settings)
    assert "sidecar check versions_agree failed [set]" in caplog.text
    assert service.describe_survey().notices[0].scope == "set"


def test_relative_folders_are_found_beside_the_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # as a desktop client starts the server
    service = holder.build_service(MetaSettings())
    assert len(service.sidecars.tables) == 48
