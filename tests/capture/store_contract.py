"""What every record store must do, whatever holds the records.

Each case takes a store and a `tag` that makes its IDs unique, so the same
cases run on a fresh folder, on a fake warehouse, and on real tables that
keep earlier runs' rows.
"""

import pytest

from ce_capture.storage import RecordExists, RecordStore


def req(rid, session, at="2026-11-20T10:00:00+00:00", **source):
    return {
        "request_id": rid,
        "captured_at": at,
        "source": {"session_id": session, **source},
        "capture": {"status": "captured"},
    }


def write_read_and_never_overwrite(store: RecordStore, tag: str = "") -> None:
    rid = f"req-1{tag}"
    record = req(rid, f"s1{tag}") | {"text": "naïve “quotes”\nand a\ttab"}
    assert store.write("request", record) == rid
    assert store.read("request", rid) == record
    with pytest.raises(RecordExists):
        store.write("request", record | {"changed": True})
    assert "changed" not in store.read("request", rid)
    assert store.read("request", f"req-404{tag}") is None
    assert store.read("request", "../etc") is None
    with pytest.raises(ValueError):
        store.write("request", req("../escape", f"s1{tag}"))
    # a read gives a copy: changing it changes nothing stored
    store.read("request", rid)["text"] = "changed"
    assert store.read("request", rid) == record


def by_session_is_oldest_first(store: RecordStore, tag: str = "") -> None:
    s1, s2 = f"s1{tag}", f"s2{tag}"
    a, b, c, d = (f"req-{x}{tag}" for x in "abcd")
    store.write("request", req(b, s1, at="2026-11-20T11:00:00+00:00"))
    store.write("request", req(a, s1, at="2026-11-20T10:00:00+00:00"))
    store.write("request", req(c, s2))
    store.write("request", req(d, s1, at="2026-11-20T11:00:00+00:00", turn=1))
    # the same time: the turn decides (none counts as 0), then the ID
    assert [r["request_id"] for r in store.by_session(s1)] == [a, b, d]
    assert store.by_session(f"nobody{tag}") == []


def pointing_to_lists_every_kind_newest_first(
    store: RecordStore, tag: str = ""
) -> None:
    s = f"s1{tag}"
    r1, r2, r3 = (f"req-{n}{tag}" for n in "123")
    res, out = f"res-1{tag}-001", f"out-1{tag}"
    store.write("request", req(r1, s))
    store.write(
        "request", req(r2, s, at="2026-11-20T12:00:00+00:00", follows_request_id=r1)
    )
    store.write(
        "request", req(r3, s, at="2026-11-20T13:00:00+00:00", corrects_request_id=r1)
    )
    store.write(
        "resolved",
        {
            "resolved_id": res,
            "request_id": r1,
            "derived_at": "2026-11-20T10:00:01+00:00",
        },
    )
    store.write(
        "outcome",
        {
            "outcome_id": out,
            "request_id": r1,
            "resolved_id": res,
            "evaluated_at": "2026-11-20T14:00:00+00:00",
            "verdict": "pass",
            "mode": "run",
            "rules_version": "1",
        },
    )
    found = [
        (k, r.get("request_id") if k == "request" else r.get(f"{k}_id"))
        for k, r in store.pointing_to(r1)
    ]
    assert found == [
        ("outcome", out),
        ("request", r3),
        ("request", r2),
        ("resolved", res),
    ]
    assert [r["resolved_id"] for r in store.resolved_for(r1)] == [res]
    assert [k for k, _ in store.pointing_to(res)] == ["outcome"]
    assert store.pointing_to(f"req-404{tag}") == []


CASES = (
    write_read_and_never_overwrite,
    by_session_is_oldest_first,
    pointing_to_lists_every_kind_newest_first,
)
