"""The storage contract on real Delta tables. Skipped unless asked for.

It writes rows to the tables the CE_CAPTURE_STORE_* settings name and removes
them afterwards, so it runs only with CE_CAPTURE_LIVE_STORE=1:

    CE_CAPTURE_LIVE_STORE=1 CE_CAPTURE_STORE=delta uv run pytest \
        tests/capture/test_delta_live.py -v

Create the tables first (`ce-capture store-ddl`).
"""

import os
import uuid

import pytest

from ce_capture.delta_store import DeltaRecordStore
from ce_capture.storage import ID_FIELD, KINDS, open_store
from tests.capture import store_contract

pytestmark = pytest.mark.skipif(
    os.getenv("CE_CAPTURE_LIVE_STORE") != "1",
    reason="writes to real tables: set CE_CAPTURE_LIVE_STORE=1",
)


@pytest.fixture(scope="module")
def live():
    store = open_store()
    assert isinstance(store, DeltaRecordStore), "set CE_CAPTURE_STORE=delta"
    tag = f"-live-{uuid.uuid4().hex[:10]}"
    yield store, tag
    for kind in KINDS:
        store.warehouse.execute(
            f"DELETE FROM {store.table(kind)} WHERE contains({ID_FIELD[kind]}, :tag)",
            {"tag": tag},
        )


@pytest.mark.parametrize("case", store_contract.CASES, ids=lambda c: c.__name__)
def test_the_store_contract(live, case):
    store, tag = live
    case(store, f"{tag}-{case.__name__[:5]}")


def test_a_second_store_reads_what_the_first_wrote(live):
    store, tag = live
    rid = f"req-x{tag}"
    record = store_contract.req(rid, f"s{tag}", turn=2) | {"text": "naïve\n“x”"}
    store.write("request", record)
    assert open_store().read("request", rid) == record
    row = store.warehouse.execute(
        "SELECT session_id, status, turn, CAST(captured_at AS STRING) AS at, "
        f"size(links) AS links FROM {store.table('request')} WHERE request_id = :id",
        {"id": rid},
    ).dicts()[0]
    assert (row["session_id"], row["status"], row["turn"]) == (
        f"s{tag}",
        "captured",
        "2",
    )
    assert row["at"].startswith("2026-11-20") and row["links"] == "0"
