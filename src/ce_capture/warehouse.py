"""A Databricks SQL warehouse, through the SQL Statement Execution API.

A narrow client, like `databricks.py`: one fixed workspace host from settings,
the statements path and nothing else, no redirects, no proxy settings from the
environment, a timeout and a cap on the answer's size. Every value travels as
a named parameter (`:name` in the statement), never inside the SQL text.
Failures become `StoreError` with no body, header or SQL in the message.

A statement waits up to 50 seconds for its answer (the API's limit), then is
polled until `timeout` seconds have passed in all, which covers a warehouse
that has to start. A statement still running then is cancelled.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import httpx

PATH = "/api/2.0/sql/statements"
MAX_RESPONSE_BYTES = 8_000_000
MAX_CHUNKS = 20
WAIT = 50  # seconds the API holds a request open, at most
RUNNING = ("PENDING", "RUNNING")
_PLAIN = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_.]{2,80}")


class StoreError(RuntimeError):
    """The record store could not be reached, or refused a statement."""


@dataclass(frozen=True)
class Result:
    columns: tuple[str, ...]
    rows: tuple[tuple[str | None, ...], ...]

    def dicts(self) -> list[dict[str, str | None]]:
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


class SqlWarehouse:
    """One SQL warehouse of one workspace."""

    def __init__(
        self,
        host: str,
        warehouse_id: str,
        token: Callable[[], str],
        client: httpx.Client | None = None,
        timeout: float = 120.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not warehouse_id or not _PLAIN.fullmatch(warehouse_id):
            raise StoreError("Not a SQL warehouse ID.")
        self.host = host
        self.warehouse_id = warehouse_id
        self.token = token
        self.timeout = timeout
        self.sleep = sleep
        self.clock = clock
        self.client = client or httpx.Client(
            timeout=WAIT + 15, follow_redirects=False, trust_env=False
        )

    def _send(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            r = self.client.request(
                method,
                f"{self.host}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self.token()}"},
            )
        except httpx.HTTPError:
            raise StoreError("The SQL warehouse could not be reached.") from None
        if r.status_code != 200:
            raise StoreError(f"The SQL warehouse answered HTTP {r.status_code}.")
        if len(r.content) > MAX_RESPONSE_BYTES:
            raise StoreError("The SQL warehouse answered with too much data.")
        try:
            body = r.json()
        except ValueError:
            raise StoreError("The SQL warehouse answered in an unknown form.") from None
        if not isinstance(body, dict):
            raise StoreError("The SQL warehouse answered in an unknown form.")
        return body

    def execute(
        self, statement: str, parameters: Mapping[str, str | None] | None = None
    ) -> Result:
        """Run one statement and return its rows, every value as text or None."""
        started = self.clock()
        body = self._send(
            "POST",
            PATH,
            {
                "warehouse_id": self.warehouse_id,
                "statement": statement,
                "parameters": [
                    {"name": name, "type": "STRING"}
                    | ({} if value is None else {"value": value})
                    for name, value in (parameters or {}).items()
                ],
                "wait_timeout": f"{int(min(WAIT, max(5, self.timeout)))}s",
                "on_wait_timeout": "CONTINUE",
                "disposition": "INLINE",
                "format": "JSON_ARRAY",
            },
        )
        pause = 0.5
        while self._state(body) in RUNNING:
            statement_id = self._id(body)
            if self.clock() - started >= self.timeout:
                self._cancel(statement_id)
                raise StoreError(
                    "The SQL warehouse did not answer in time; the statement "
                    "was cancelled."
                )
            self.sleep(pause)
            pause = min(pause * 2, 5.0)
            body = self._send("GET", f"{PATH}/{statement_id}")
        state = self._state(body)
        if state != "SUCCEEDED":
            code = ((body.get("status") or {}).get("error") or {}).get("error_code")
            known = isinstance(code, str) and _ERROR_CODE.fullmatch(code)
            detail = f" ({code})" if known else ""
            raise StoreError(f"The statement ended as {state}{detail}.")
        return self._result(body)

    @staticmethod
    def _state(body: dict) -> str:
        state = (body.get("status") or {}).get("state")
        if not isinstance(state, str) or not _PLAIN.fullmatch(state):
            raise StoreError("The SQL warehouse answered in an unknown form.")
        return state

    @staticmethod
    def _id(body: dict) -> str:
        statement_id = body.get("statement_id")
        if not isinstance(statement_id, str) or not _PLAIN.fullmatch(statement_id):
            raise StoreError("The SQL warehouse answered in an unknown form.")
        return statement_id

    def _cancel(self, statement_id: str) -> None:
        try:
            self._send("POST", f"{PATH}/{statement_id}/cancel")
        except StoreError:
            pass  # the timeout is what the caller is told

    def _result(self, body: dict) -> Result:
        manifest = body.get("manifest") or {}
        columns = tuple(
            c.get("name", "") for c in (manifest.get("schema") or {}).get("columns", [])
        )
        if manifest.get("truncated"):
            raise StoreError("The SQL warehouse answered with too much data.")
        rows: list[tuple] = []
        chunk = body.get("result") or {}
        for _ in range(MAX_CHUNKS):
            rows.extend(tuple(row) for row in chunk.get("data_array") or [])
            following = chunk.get("next_chunk_index")
            if following is None:
                return Result(columns, tuple(rows))
            if not isinstance(following, int) or following < 0:
                raise StoreError("The SQL warehouse answered in an unknown form.")
            chunk = self._send(
                "GET", f"{PATH}/{self._id(body)}/result/chunks/{following}"
            )
        raise StoreError("The SQL warehouse answered with too much data.")
