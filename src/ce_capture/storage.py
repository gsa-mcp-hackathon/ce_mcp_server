"""Storage of request, resolved and outcome records (D17).

One interface with four operations: write a record, read a record by ID, list
records by session, and list the records that point to a given record. No
record is ever updated: a write of an ID that exists is refused. The JSON
file store is the local implementation; `delta_store.DeltaRecordStore` keeps
the same records in Delta tables on Databricks. `open_store` returns the one
the settings name.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from ce_capture.config import CaptureSettings

Kind = Literal["request", "resolved", "outcome"]
KINDS: tuple[Kind, ...] = ("request", "resolved", "outcome")
ID_FIELD = {"request": "request_id", "resolved": "resolved_id", "outcome": "outcome_id"}
TIME_FIELD = {
    "request": "captured_at",
    "resolved": "derived_at",
    "outcome": "evaluated_at",
}
# The fields through which one record points to another.
LINK_FIELDS = (
    ("source", "follows_request_id"),
    ("source", "answers_request_id"),
    ("source", "corrects_request_id"),
    (None, "request_id"),
    (None, "resolved_id"),
)


class RecordExists(ValueError):
    """Records are never changed: an ID can be written once."""


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def _links(kind: Kind, record: dict) -> set[str]:
    out = set()
    for section, name in LINK_FIELDS:
        holder = record.get(section) if section else record
        value = (holder or {}).get(name)
        if value and value != record.get(ID_FIELD[kind]):
            out.add(value)
    return out


def check_id(record_id: object) -> str:
    """A record ID as given, or ValueError: it is used as a file name."""
    if (
        not isinstance(record_id, str)
        or not record_id
        or "/" in record_id
        or record_id.startswith(".")
    ):
        raise ValueError(f"not a record ID: {record_id!r}")
    return record_id


def oldest_first(kind: Kind, rows: list[dict]) -> list[dict]:
    """A session's records in order: by time, then turn, then ID."""

    def order(r: dict) -> tuple:
        turn = (r.get("source") or {}).get("turn") or 0
        return (r.get(TIME_FIELD[kind]) or "", turn, r[ID_FIELD[kind]])

    return sorted(rows, key=order)


def newest_first(found: list[tuple[Kind, dict]]) -> list[tuple[Kind, dict]]:
    return sorted(
        found, key=lambda kr: kr[1].get(TIME_FIELD[kr[0]]) or "", reverse=True
    )


class RecordStore(Protocol):
    def write(self, kind: Kind, record: dict) -> str: ...

    def read(self, kind: Kind, record_id: str) -> dict | None: ...

    def by_session(self, session_id: str, kind: Kind = "request") -> list[dict]: ...

    def pointing_to(self, record_id: str) -> list[tuple[Kind, dict]]: ...


class JsonFileStore:
    """One JSON file per record, under `<folder>/<kind>/<id>.json`."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    def _path(self, kind: Kind, record_id: str) -> Path:
        return self.folder / kind / f"{check_id(record_id)}.json"

    def write(self, kind: Kind, record: dict) -> str:
        record_id = record[ID_FIELD[kind]]
        path = self._path(kind, record_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        try:
            # a hard link fails if the name exists, so two writers cannot both win
            os.link(tmp, path)
        except FileExistsError:
            raise RecordExists(f"{kind} record {record_id} exists already") from None
        finally:
            tmp.unlink(missing_ok=True)
        return record_id

    def read(self, kind: Kind, record_id: str) -> dict | None:
        try:
            path = self._path(kind, record_id)
        except ValueError:
            return None
        return json.loads(path.read_text()) if path.exists() else None

    def _all(self, kind: Kind) -> list[dict]:
        folder = self.folder / kind
        if not folder.exists():
            return []
        return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]

    def by_session(self, session_id: str, kind: Kind = "request") -> list[dict]:
        """A session's records, oldest first."""
        rows = [
            r
            for r in self._all(kind)
            if (r.get("source") or {}).get("session_id") == session_id
        ]
        return oldest_first(kind, rows)

    def pointing_to(self, record_id: str) -> list[tuple[Kind, dict]]:
        """Every record that names `record_id`, newest first."""
        found = []
        for kind in KINDS:
            for r in self._all(kind):
                if record_id in _links(kind, r):
                    found.append((kind, r))
        return newest_first(found)

    def resolved_for(self, request_id: str) -> list[dict]:
        """A request's resolved records, newest first."""
        return [r for kind, r in self.pointing_to(request_id) if kind == "resolved"]


def open_store(
    settings: CaptureSettings | None = None, records_dir: Path | None = None
) -> RecordStore:
    """The record store the settings name: JSON files, or Delta tables.

    `records_dir` replaces the settings' folder for the JSON store, for a
    caller that has already located it.
    """
    from ce_capture.config import CaptureSettings

    settings = settings or CaptureSettings()
    if settings.store == "delta":
        from ce_capture.delta_store import DeltaRecordStore

        return DeltaRecordStore.from_settings(settings)
    return JsonFileStore(records_dir or settings.records_dir)
