"""Status rule, table-driven. Each case is a history of events; every case runs in the given order AND reversed,
and must give the same answer (arrival order never matters). U = upsert with data, D = upsert without data,
S = struck-off, the number is the day in January 2026, `?` = no usable timestamp."""

import json

import pytest

from mle.normalize import parse_message
from mle.repository import process_batch
from tests.integration.conftest import CountingEmbedder, make_event

REG = "SYN-000001"


def event(code: str):
    kind, day = code[0], code[1:]
    ts = None if day == "?" else f"2026-01-{int(day):02d}T00:00:00+08:00"
    if kind == "U":
        return make_event(REG, ts, companyName=f"U{day}", natureOfBusiness="Some business")
    if kind == "S":
        return make_event(REG, ts, kind="company-struck-off")
    payload = {"type": "company-upserted", "registrationNumber": REG}
    if ts:
        payload["updatedAt"] = ts
    return parse_message(None, json.dumps(payload).encode())  # D: no `data`


CASES = [
    ("U1", "active"), ("S2", "struck_off"), ("D1", "active"),
    ("U1 S2", "struck_off"), ("U3 S2", "active"), ("S2 U3", "active"), ("S2 U1", "struck_off"),
    ("S2 S3", "struck_off"), ("S2 D3", "struck_off"), ("U1 D2", "active"), ("D2 U1", "active"),
    ("U1 S2 U3 S4", "struck_off"), ("U1 S2 U3 S4 U5", "active"), ("S4 U3 S2 U1", "struck_off"),
    ("U1 U2 U3", "active"), ("S1 S2 S3 U4", "active"), ("U1 S2 D3 D4", "struck_off"),
    ("U? S1", "struck_off"), ("S? U1", "active"), ("U1 S?", "active"), ("S1 U?", "struck_off"),
]  # fmt: skip


@pytest.mark.parametrize("reverse", [False, True], ids=["in-order", "reversed"])
@pytest.mark.parametrize(("history", "expected"), CASES, ids=[c[0] for c in CASES])
def test_status_follows_the_newest_status_bearing_event(conn, history, expected, reverse):
    events = [event(code) for code in history.split()]
    for e in reversed(events) if reverse else events:
        process_batch(conn, CountingEmbedder(), [e])
    assert conn.execute("SELECT status FROM companies").fetchone()[0] == expected


@pytest.mark.parametrize("history", [c[0] for c in CASES])
def test_status_is_the_same_when_the_whole_history_arrives_in_one_batch(conn, history):
    events = [event(code) for code in history.split()]
    process_batch(conn, CountingEmbedder(), events)
    one_batch = conn.execute("SELECT status FROM companies").fetchone()[0]
    conn.execute("TRUNCATE company_events, companies")
    for e in events:
        process_batch(conn, CountingEmbedder(), [e])
    assert conn.execute("SELECT status FROM companies").fetchone()[0] == one_batch
