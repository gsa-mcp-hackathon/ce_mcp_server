"""The three resources, read the way a client reads them."""

import json

import pytest
from fastmcp import Client

from ce_mcp_server import service as holder
from ce_mcp_server.app import mcp


async def read(uri: str) -> tuple[dict, str]:
    async with Client(mcp) as client:
        contents = await client.read_resource(uri)
    return json.loads(contents[0].text), contents[0].mime_type


@pytest.fixture
def built(built_service):
    holder.set_service(built_service)
    yield
    holder.set_service(None)


@pytest.fixture
def unbuilt(meta_service):
    holder.set_service(meta_service)
    yield
    holder.set_service(None)


async def test_the_resources_are_listed():
    async with Client(mcp) as client:
        fixed = {str(r.uri) for r in await client.list_resources()}
        templates = {t.uri_template for t in await client.list_resource_templates()}
    assert fixed == {"ce://geography/codes", "ce://methods/weighting"}
    assert templates == {"ce://dictionary/{year}"}


async def test_weighting_method(built):
    answer, mime = await read("ce://methods/weighting")
    assert mime == "application/json"
    assert answer["weight"]["value"]["column"] == "finlwt21"
    assert answer["variance"]["value"]["byRelease"][0]["method"] == "fay"
    assert answer["replicate_weights"]["implies"]


async def test_geography_codes_gives_every_level_with_its_support(built):
    answer, _ = await read("ce://geography/codes")
    assert list(answer) == ["region", "division", "state", "psu"]
    assert {level: a["support"] for level, a in answer.items()} == {
        "region": "reliable",
        "division": "reliable",
        "state": "limited",
        "psu": "unsupported",
    }
    assert answer["state"]["entries"][1] == {
        "code": "06",
        "label": "California",
        "parent_division": "9",
        "parent_region": "4",
        "interviews": 5,
    }


async def test_dictionary_by_year(built):
    answer, _ = await read("ce://dictionary/2025")
    assert answer["year"] == 2025
    assert answer["tables"]["mtbi"]["cost"] == "Total cost of item"
    past, _ = await read("ce://dictionary/1990")
    assert past["tables"]["fmli"]["oldvar"] == "Dropped long ago"


async def test_an_earlier_year_before_the_tables_are_built_is_an_error(unbuilt):
    answer, _ = await read("ce://dictionary/1990")
    assert "ce-meta build" in answer["error"]
    assert answer["suggestions"] == ["2025"]


async def test_a_year_the_dictionary_does_not_reach(built):
    answer, _ = await read("ce://dictionary/1900")
    assert answer == {
        "error": "The dictionary does not reach 1900.",
        "suggestions": ["1984"],
    }
