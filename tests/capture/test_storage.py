"""The JSON file store (`ce_capture.storage`)."""

import pytest

from ce_capture.config import CaptureSettings
from ce_capture.storage import JsonFileStore, new_request_id, open_store
from tests.capture import store_contract


@pytest.mark.parametrize("case", store_contract.CASES, ids=lambda c: c.__name__)
def test_the_store_contract(tmp_path, case):
    case(JsonFileStore(tmp_path))


def test_one_file_per_record_and_no_path_escapes(tmp_path):
    store = JsonFileStore(tmp_path)
    store_contract.write_read_and_never_overwrite(store)
    assert [p.name for p in (tmp_path / "request").iterdir()] == ["req-1.json"]


def test_open_store_gives_json_files_unless_delta_is_asked_for(tmp_path):
    store = open_store(CaptureSettings(_env_file=None, records_dir=tmp_path))
    assert isinstance(store, JsonFileStore) and store.folder == tmp_path
    located = open_store(CaptureSettings(_env_file=None), records_dir=tmp_path / "x")
    assert located.folder == tmp_path / "x"


def test_request_ids_are_unique():
    assert len({new_request_id() for _ in range(200)}) == 200
