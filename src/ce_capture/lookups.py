"""The lookup tools given to the server-side model (D19).

The same six functions the MCP server exposes, called directly on the metadata
service, with the same answers: what a client's model sees through MCP, the
server's model sees here. Plus `submit_record`, the tool the model returns its
record through, whose parameters are the model-written part of the schema.
"""

from __future__ import annotations

import json
from typing import Any

from ce_capture.records import ModelOutput
from ce_meta.errors import CeMetaError
from ce_meta.service import MetadataService

SUBMIT = "submit_record"

_YEAR = {
    "type": ["integer", "null"],
    "description": "Survey year; default: the release.",
}
_TABLE = {"type": ["string", "null"], "description": "A stored table, such as fmli."}

SPECS: dict[str, dict] = {
    "describe_survey": {
        "description": "The survey facts every request depends on: the weight, the "
        "reference period, the geography ratings, the breaks and the constraints. "
        "Call it first.",
        "parameters": {"type": "object", "properties": {}},
    },
    "list_datasets": {
        "description": "The CE Interview tables: what one row of each is, and its "
        "title.",
        "parameters": {"type": "object", "properties": {"year": _YEAR}},
    },
    "describe_table": {
        "description": "One table: what a row is, its row key, and how it reaches the "
        "weight.",
        "parameters": {
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        },
    },
    "search_variables": {
        "description": "Find columns by name or by what they describe. Each match "
        "says how a request may use it (usable_as) and what is under review.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "table": _TABLE,
                "year": _YEAR,
                "usable_as": {
                    "type": ["string", "null"],
                    "enum": ["measure", "grouping", "filter", None],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
    },
    "describe_variable": {
        "description": "One column: definition, codes and labels, facts, caveats. Use "
        "code_query to find a code by its label in a long list.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "table": _TABLE,
                "year": _YEAR,
                "code_query": {"type": ["string", "null"]},
                "code_limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["name"],
        },
    },
    "list_geographies": {
        "description": "The codes of one geography level, the column that carries it, "
        "and how far the design supports it.",
        "parameters": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "string",
                    "enum": ["region", "division", "state", "psu"],
                },
                "year": _YEAR,
            },
            "required": ["level"],
        },
    },
}


def tool_list(include_lookups: bool = True) -> list[dict]:
    """The tools offered to the model: the lookups, then submit_record."""
    tools = []
    if include_lookups:
        tools += [
            {"type": "function", "function": {"name": name, **spec}}
            for name, spec in SPECS.items()
        ]
    tools.append(
        {
            "type": "function",
            "function": {
                "name": SUBMIT,
                "description": "Submit the request record. The answer says whether it "
                "passed the check; if not, correct it and submit it again.",
                "parameters": ModelOutput.model_json_schema(),
            },
        }
    )
    return tools


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json", exclude_none=True, exclude_defaults=True)


def run_lookup(service: MetadataService, name: str, arguments: object) -> dict:
    """Run one lookup and return its answer, or an error the model can act on."""
    if name not in SPECS:
        return {"error": f"There is no tool {name!r}.", "suggestions": list(SPECS)}
    args = arguments if isinstance(arguments, dict) else {}
    a = {k: v for k, v in args.items() if v is not None}
    try:
        if name == "describe_survey":
            result = service.describe_survey()
        elif name == "list_datasets":
            result = service.list_datasets(a.get("year"))
        elif name == "describe_table":
            result = service.describe_table(str(a.get("table", "")))
        elif name == "search_variables":
            result = service.search_variables(
                str(a.get("query", "")),
                a.get("table"),
                a.get("year"),
                a.get("usable_as"),
                int(a.get("limit", 20)),
            )
        elif name == "describe_variable":
            result = service.describe_variable(
                str(a.get("name", "")),
                a.get("table"),
                a.get("year"),
                a.get("code_query"),
                int(a.get("code_limit", 50)),
            )
        else:
            result = service.list_geographies(a.get("level"), a.get("year"))
    except CeMetaError as exc:
        return {"error": exc.message, "suggestions": exc.suggestions}
    except (TypeError, ValueError) as exc:
        return {"error": f"Bad arguments for {name}: {exc}"}
    return _dump(result)


def as_content(answer: object) -> str:
    return json.dumps(answer, ensure_ascii=False)
