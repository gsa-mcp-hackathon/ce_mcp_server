"""The Delta record store (`ce_capture.delta_store`), on a fake warehouse.

The fake keeps rows in memory and answers by the shape of each statement, so
the storage contract runs with no workspace; the statements and parameters
are checked apart. `test_delta_live.py` runs the same contract on real tables.
"""

import json
import re

import pytest

from ce_capture.config import CaptureSettings
from ce_capture.delta_store import DeltaRecordStore
from ce_capture.storage import RecordExists, open_store
from ce_capture.warehouse import Result, StoreError
from tests.capture import store_contract

T = "`cat`.`sch`.`ce_mcp_{}_records`"


class FakeWarehouse:
    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict]] = {}
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, parameters=None):
        p = dict(parameters or {})
        self.calls.append((statement, p))
        assert all(v is None or isinstance(v, str) for v in p.values())
        if statement.startswith("MERGE INTO"):
            table = re.match(r"MERGE INTO (\S+) ", statement).group(1)
            rows = self.tables.setdefault(table, {})
            new = p["id"] not in rows
            if new:
                rows[p["id"]] = p | {"links": json.loads(p["links"])}
            return Result(
                ("num_affected_rows", "num_inserted_rows"), ((str(int(new)),) * 2,)
            )
        found = []
        for part in statement.split(" UNION ALL "):
            table = re.search(r"FROM (\S+) WHERE", part).group(1)
            kind = re.match(r"SELECT '(\w+)' AS kind", part)
            for row in self.tables.get(table, {}).values():
                if "array_contains(links, :id)" in part:
                    keep = p["id"] in row["links"]
                elif "session_id = :session" in part:
                    keep = row.get("session_id") == p["session"]
                elif "WHERE request_id = :id" in part and "resolved" in table:
                    keep = row.get("request_id") == p["id"]
                else:
                    keep = row["id"] == p["id"]
                if keep:
                    found.append(((kind.group(1),) if kind else ()) + (row["record"],))
        return Result(
            ("kind", "record") if "UNION" in statement else ("record",), tuple(found)
        )


@pytest.fixture
def warehouse():
    return FakeWarehouse()


@pytest.fixture
def store(warehouse):
    return DeltaRecordStore(warehouse, "cat", "sch")


@pytest.mark.parametrize("case", store_contract.CASES, ids=lambda c: c.__name__)
def test_the_store_contract(store, case):
    case(store)


def test_a_write_is_one_merge_with_every_value_a_parameter(store, warehouse):
    record = store_contract.req(
        "req-1", "s1", turn=3, follows_request_id="req-0", note="it's; DROP"
    )
    store.write("request", record)
    statement, p = warehouse.calls[-1]
    assert statement == (
        f"MERGE INTO {T.format('request')} AS t USING (SELECT :id AS id) AS s "
        "ON t.request_id = s.id WHEN NOT MATCHED THEN INSERT "
        "(request_id, session_id, status, turn, captured_at, links, record) VALUES "
        "(s.id, :session_id, :status, try_cast(:turn AS INT), "
        "try_cast(:at AS TIMESTAMP), from_json(:links, 'array<string>'), :record)"
    )
    assert p == {
        "id": "req-1",
        "session_id": "s1",
        "status": "captured",
        "turn": "3",
        "at": "2026-11-20T10:00:00+00:00",
        "links": '["req-0"]',
        "record": json.dumps(record, ensure_ascii=False),
    }
    assert "DROP" not in statement


def test_an_outcome_carries_its_columns_and_a_missing_one_is_null(store, warehouse):
    store.write("outcome", {"outcome_id": "out-1", "request_id": "req-1"})
    statement, p = warehouse.calls[-1]
    assert "(outcome_id, request_id, resolved_id, verdict, mode, rules_version, " in (
        statement
    )
    assert (p["request_id"], p["verdict"], p["at"]) == ("req-1", None, None)
    assert p["links"] == '["req-1"]'


def test_a_record_read_or_written_is_not_read_again(store, warehouse):
    store.write("request", store_contract.req("req-1", "s1"))
    store.write("request", store_contract.req("req-2", "s1"))
    fresh = DeltaRecordStore(warehouse, "cat", "sch")
    n = len(warehouse.calls)
    assert store.read("request", "req-1")["request_id"] == "req-1"  # written here
    assert len(warehouse.calls) == n
    assert fresh.read("request", "req-1")["request_id"] == "req-1"
    assert fresh.read("request", "req-1")["request_id"] == "req-1"
    assert len(warehouse.calls) == n + 1
    assert warehouse.calls[-1] == (
        f"SELECT record FROM {T.format('request')} WHERE request_id = :id LIMIT 1",
        {"id": "req-1"},
    )
    fresh.by_session("s1")  # a listing fills the cache too
    n = len(warehouse.calls)
    fresh.read("request", "req-2")
    assert len(warehouse.calls) == n
    # a miss is not remembered: the record may be written later
    assert fresh.read("request", "req-9") is None
    store.write("request", store_contract.req("req-9", "s1"))
    assert fresh.read("request", "req-9") is not None


def test_pointing_to_is_one_statement_over_the_three_tables(store, warehouse):
    store.pointing_to("req-1")
    statement, p = warehouse.calls[-1]
    assert p == {"id": "req-1"}
    assert statement.count(" UNION ALL ") == 2
    for kind in ("request", "resolved", "outcome"):
        assert (
            f"SELECT '{kind}' AS kind, record FROM {T.format(kind)} "
            "WHERE array_contains(links, :id)"
        ) in statement


def test_only_requests_have_a_session(store, warehouse):
    assert store.by_session("s1", "outcome") == []
    assert warehouse.calls == []


def test_a_merge_that_does_not_say_what_it_wrote_is_an_error(warehouse):
    class Silent(FakeWarehouse):
        def execute(self, statement, parameters=None):
            return Result((), ())

    with pytest.raises(StoreError, match="whether it wrote"):
        DeltaRecordStore(Silent(), "cat", "sch").write(
            "request", store_contract.req("req-1", "s1")
        )


def test_a_stored_record_that_is_not_json_is_an_error(store, warehouse):
    warehouse.tables[T.format("request")] = {
        "req-1": {"id": "req-1", "record": "not json", "links": []}
    }
    with pytest.raises(StoreError, match="not JSON"):
        store.read("request", "req-1")


@pytest.mark.parametrize(
    ("catalog", "schema", "prefix"),
    [
        ("", "sch", "p_"),
        ("cat", "", "p_"),
        ("cat`; DROP", "sch", "p_"),
        ("cat", "a.b", "p_"),
        ("cat", "sch", "p-"),
        ("cat", "sch", "p`"),
    ],
)
def test_names_are_plain_identifiers(warehouse, catalog, schema, prefix):
    with pytest.raises(StoreError):
        DeltaRecordStore(warehouse, catalog, schema, prefix)


def test_the_ddl_and_the_grants(warehouse):
    store = DeltaRecordStore(warehouse, "cat", "sch", "")
    ddl = store.ddl()
    assert [s.splitlines()[0] for s in ddl] == [
        "CREATE TABLE IF NOT EXISTS `cat`.`sch`.`request_records` (",
        "CREATE TABLE IF NOT EXISTS `cat`.`sch`.`resolved_records` (",
        "CREATE TABLE IF NOT EXISTS `cat`.`sch`.`outcome_records` (",
    ]
    assert "  links ARRAY<STRING>,\n  record STRING NOT NULL\n)" in ddl[0]
    grants = store.grants("1234-abcd")
    assert grants[0] == "GRANT USE CATALOG ON CATALOG `cat` TO `1234-abcd`"
    assert len(grants) == 5 and all("`1234-abcd`" in g for g in grants)
    with pytest.raises(StoreError):
        store.grants("x`; DROP")
    assert warehouse.calls == []


def test_the_delta_store_says_what_it_lacks(monkeypatch):
    monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    base = {"_env_file": None, "store": "delta"}
    with pytest.raises(StoreError, match="CE_CAPTURE_WAREHOUSE_ID"):
        open_store(CaptureSettings(**base))
    with pytest.raises(StoreError, match="host"):
        open_store(CaptureSettings(**base, warehouse_id="wh-1"))
    monkeypatch.setenv("DATABRICKS_TOKEN", "tok")
    host = {"databricks_host": "https://adb-1.2.azuredatabricks.net"}
    with pytest.raises(StoreError, match="CE_CAPTURE_STORE_CATALOG"):
        open_store(CaptureSettings(**base, **host, warehouse_id="wh-1"))
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh-env")
    store = open_store(
        CaptureSettings(**base, **host, store_catalog="cat", store_schema="sch")
    )
    assert isinstance(store, DeltaRecordStore)
    assert store.warehouse.warehouse_id == "wh-env"
    assert store.table("request") == T.format("request")


def test_a_refused_write_changes_nothing(store):
    record = store_contract.req("req-1", "s1")
    store.write("request", record)
    with pytest.raises(RecordExists):
        DeltaRecordStore(store.warehouse, "cat", "sch").write(
            "request", record | {"changed": True}
        )
    assert "changed" not in store.read("request", "req-1")


def test_store_ddl_prints_and_contacts_nothing(capsys, monkeypatch):
    from ce_capture.cli import main

    monkeypatch.delenv("CE_CAPTURE_STORE_CATALOG", raising=False)
    args = ["store-ddl", "--catalog", "cat", "--schema", "sch"]
    assert main([*args, "--grant-to", "1234-abcd"]) == 0
    out = capsys.readouterr().out
    assert out.count("CREATE TABLE IF NOT EXISTS `cat`.`sch`.`ce_mcp_") == 3
    assert out.count("GRANT ") == 5 and out.rstrip().endswith(";")
    assert main(["store-ddl", "--catalog", "a.b", "--schema", "sch"]) == 1
    assert "plain catalog name" in capsys.readouterr().err
