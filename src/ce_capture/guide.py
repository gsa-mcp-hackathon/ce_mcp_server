"""The capture instructions and the guide a client's model reads (D27)."""

from __future__ import annotations

import functools
import re

from ce_capture.context import CaptureContext
from ce_capture.records import ModelOutput
from ce_capture.rules import DATA, load_defaults, load_phrases

INSTRUCTIONS = DATA / "instructions.md"
AFTER_CAPTURE = DATA / "after_capture.md"


@functools.cache
def instructions() -> tuple[str, str]:
    """(version, text) of the capture instructions, without the header comment."""
    raw = INSTRUCTIONS.read_text()
    m = re.search(r"version (\d+\.\d+)", raw)
    version = m[1] if m else "0"
    text = re.sub(r"^<!--.*?-->\s*", "", raw, flags=re.S)
    return version, text


@functools.cache
def after_capture() -> tuple[str, str]:
    """(version, text) of the guidance for after a record is stored: what to
    do with each status, with a block, and with the server's figures. For a
    client's model only; the server-side capture model only writes the record."""
    raw = AFTER_CAPTURE.read_text()
    m = re.search(r"version (\d+\.\d+)", raw)
    version = m[1] if m else "0"
    return version, re.sub(r"^<!--.*?-->\s*", "", raw, flags=re.S)


def capture_guide(ctx: CaptureContext, include_schema: bool = True) -> dict:
    """What a client's model needs before it writes a request record."""
    version, text = instructions()
    after_version, after = after_capture()
    guide = {
        "instructions_version": version,
        "instructions": text,
        "after_capture_version": after_version,
        "after_capture": after,
        "tables": ctx.covered_tables,
        "loaded_years": ctx.loaded_years,
        "defaults_version": load_defaults().version,
        "phrases_version": load_phrases().version,
        "summary": (
            f"Capture covers {len(ctx.covered_tables)} CE Interview tables. Follow "
            "the instructions, use the lookup tools for every table and column, "
            "and submit the record with submit_request_record. Then follow "
            "after_capture: run a captured request, and treat a block as the answer."
        ),
    }
    if include_schema:
        guide["record_schema"] = ModelOutput.model_json_schema()
    return guide
