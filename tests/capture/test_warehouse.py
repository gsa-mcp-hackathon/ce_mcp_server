"""The SQL warehouse client (`ce_capture.warehouse`), against a mock."""

import json

import httpx
import pytest

from ce_capture.warehouse import PATH, SqlWarehouse, StoreError

HOST = "https://adb-1.2.azuredatabricks.net"


def _warehouse(handler, **kwargs):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    return SqlWarehouse(HOST, "wh-1", lambda: "tok", client, **kwargs)


def _done(rows, columns=("record",), **result):
    return {
        "statement_id": "01f0-abc",
        "status": {"state": "SUCCEEDED"},
        "manifest": {"schema": {"columns": [{"name": c} for c in columns]}},
        "result": {"data_array": rows, **result},
    }


def test_the_request_carries_values_as_parameters_only():
    seen = {}

    def handler(request):
        seen["method"], seen["url"] = request.method, str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_done([["a", None]], ("x", "y")))

    result = _warehouse(handler).execute(
        "SELECT :a AS x, :b AS y", {"a": "it's; DROP TABLE t", "b": None}
    )
    assert (seen["method"], seen["url"]) == ("POST", HOST + PATH)
    assert seen["auth"] == "Bearer tok"
    body = seen["body"]
    assert body["statement"] == "SELECT :a AS x, :b AS y"
    assert body["parameters"] == [
        {"name": "a", "type": "STRING", "value": "it's; DROP TABLE t"},
        {"name": "b", "type": "STRING"},  # no value: a null
    ]
    assert (body["warehouse_id"], body["wait_timeout"]) == ("wh-1", "50s")
    assert (body["disposition"], body["format"]) == ("INLINE", "JSON_ARRAY")
    assert body["on_wait_timeout"] == "CONTINUE"
    assert result.dicts() == [{"x": "a", "y": None}]


def test_a_statement_still_running_is_polled():
    calls, naps = [], []

    def handler(request):
        calls.append((request.method, request.url.path))
        if len(calls) < 3:
            state = "PENDING" if len(calls) == 1 else "RUNNING"
            return httpx.Response(
                200, json={"statement_id": "01f0-abc", "status": {"state": state}}
            )
        return httpx.Response(200, json=_done([["r"]]))

    result = _warehouse(handler, sleep=naps.append).execute("SELECT 1")
    assert calls == [
        ("POST", PATH),
        ("GET", f"{PATH}/01f0-abc"),
        ("GET", f"{PATH}/01f0-abc"),
    ]
    assert naps == [0.5, 1.0]
    assert result.rows == (("r",),)


def test_a_statement_that_never_ends_is_cancelled():
    calls = []
    now = iter(range(0, 1000, 40))

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200, json={"statement_id": "01f0-abc", "status": {"state": "RUNNING"}}
        )

    warehouse = _warehouse(
        handler, timeout=100, sleep=lambda s: None, clock=lambda: next(now)
    )
    with pytest.raises(StoreError, match="in time"):
        warehouse.execute("SELECT 1")
    assert calls[-1] == ("POST", f"{PATH}/01f0-abc/cancel")


def test_more_rows_are_read_chunk_by_chunk():
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=_done([["a"]], next_chunk_index=1))
        assert request.url.path == f"{PATH}/01f0-abc/result/chunks/1"
        return httpx.Response(200, json={"data_array": [["b"]]})

    assert _warehouse(handler).execute("SELECT 1").rows == (("a",), ("b",))


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(403, text="secret detail"), "HTTP 403"),
        (httpx.Response(200, text="<html>secret detail"), "unknown form"),
        (httpx.Response(200, json=["secret detail"]), "unknown form"),
        (httpx.Response(200, json={"status": {}}), "unknown form"),
        (
            httpx.Response(
                200,
                json={
                    "statement_id": "s",
                    "status": {
                        "state": "FAILED",
                        "error": {
                            "error_code": "TABLE_OR_VIEW_NOT_FOUND",
                            "message": "secret detail",
                        },
                    },
                },
            ),
            r"FAILED \(TABLE_OR_VIEW_NOT_FOUND\)",
        ),
        (
            httpx.Response(
                200,
                json={
                    "statement_id": "s",
                    "status": {
                        "state": "FAILED",
                        "error": {"error_code": "secret detail"},
                    },
                },
            ),
            r"FAILED\.$",
        ),
        (
            httpx.Response(
                200,
                json=_done([["a"]]) | {"manifest": {"truncated": True}},
            ),
            "too much data",
        ),
    ],
)
def test_failures_are_sanitized(response, message):
    with pytest.raises(StoreError, match=message) as caught:
        _warehouse(lambda request: response).execute("SELECT secret_sql")
    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_a_network_failure_and_a_redirect_are_errors():
    def down(request):
        raise httpx.ConnectError("secret detail")

    with pytest.raises(StoreError, match="could not be reached") as caught:
        _warehouse(down).execute("SELECT 1")
    assert "secret" not in str(caught.value)

    def redirect(request):
        return httpx.Response(302, headers={"location": "https://elsewhere.example"})

    with pytest.raises(StoreError, match="HTTP 302"):
        _warehouse(redirect).execute("SELECT 1")


def test_too_large_an_answer_is_refused(monkeypatch):
    monkeypatch.setattr("ce_capture.warehouse.MAX_RESPONSE_BYTES", 10)
    with pytest.raises(StoreError, match="too much data"):
        _warehouse(lambda r: httpx.Response(200, json=_done([["a"]]))).execute("S")


@pytest.mark.parametrize("warehouse_id", ["", "a/b", "../x", "a b", "a?b=c"])
def test_a_warehouse_id_is_a_plain_name(warehouse_id):
    with pytest.raises(StoreError):
        SqlWarehouse(HOST, warehouse_id, lambda: "tok")


def test_an_odd_statement_id_is_never_put_in_a_path():
    def handler(request):
        return httpx.Response(
            200, json={"statement_id": "../../x", "status": {"state": "RUNNING"}}
        )

    with pytest.raises(StoreError, match="unknown form"):
        _warehouse(handler, sleep=lambda s: None).execute("SELECT 1")
