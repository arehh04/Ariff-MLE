"""Write path: projection rules, rejects, struck-off, embeddings, failure isolation (test plan items 1, 5-9)."""

import json
from pathlib import Path

import psycopg
import pytest

from mle.normalize import Event, Rejected, parse_message
from mle.repository import process_batch, process_batch_isolated
from tests.integration.conftest import CountingEmbedder, make_event

REG = "SYN-000001"
FEED = Path(__file__).resolve().parents[2] / "data" / "feed.jsonl"


def company(conn, reg: str = REG) -> dict:
    cur = conn.execute("SELECT * FROM companies WHERE registration_number = %s", (reg,))
    row = cur.fetchone()
    assert row is not None, f"{reg} not stored"
    return dict(zip([c.name for c in cur.description], row, strict=True))


def run(conn, embedder, *events):
    return process_batch(conn, embedder, list(events))


# ---- 1. projection: newest wins, order does not matter ----------------------------------------------------------


def test_newer_replaces_older(conn, embedder):
    run(conn, embedder, make_event(REG, "2026-01-01T00:00:00+08:00", companyName="Old", state="Perak"))
    run(conn, embedder, make_event(REG, "2026-02-01T00:00:00+08:00", companyName="New", state="Perak"))
    assert company(conn)["company_name"] == "New"


def test_older_arriving_late_is_ignored(conn, embedder):
    run(conn, embedder, make_event(REG, "2026-02-01T00:00:00+08:00", companyName="New"))
    run(conn, embedder, make_event(REG, "2026-01-01T00:00:00+08:00", companyName="Old"))
    assert company(conn)["company_name"] == "New"


def test_newer_null_does_not_erase(conn, embedder):
    run(
        conn,
        embedder,
        make_event(REG, "2026-01-01T00:00:00+08:00", companyName="A", state="Perak", industry="Retail"),
    )
    run(
        conn, embedder, make_event(REG, "2026-02-01T00:00:00+08:00", companyName="B", state=None, industry="")
    )
    row = company(conn)
    assert (row["company_name"], row["state"], row["industry"]) == ("B", "Perak", "Retail")


def test_missing_timestamp_never_beats_a_real_one(conn, embedder):
    run(conn, embedder, make_event(REG, "2026-01-01T00:00:00+08:00", companyName="Real"))
    run(conn, embedder, make_event(REG, None, companyName="NoTime"))
    row = company(conn)
    assert row["company_name"] == "Real" and row["updated_at_missing"] is False


def test_equal_timestamps_resolve_the_same_in_both_orders(conn, embedder):
    a = make_event(REG, "2026-01-01T00:00:00+08:00", companyName="Alpha")
    b = make_event(REG, "2026-01-01T00:00:00+08:00", companyName="Beta")
    run(conn, embedder, a, b)
    first = company(conn)["company_name"]
    conn.execute("TRUNCATE company_events, companies")
    run(conn, embedder, b, a)
    assert company(conn)["company_name"] == first


# ---- 5. no lost companies ----------------------------------------------------------------------------------------


def test_every_feed_line_is_accounted_for(conn, embedder):
    lines = FEED.read_text(encoding="utf-8").splitlines()
    parsed = [parse_message(None, line.encode()) for line in lines]
    for start in range(0, len(parsed), 100):
        process_batch_isolated(conn, embedder, parsed[start : start + 100])

    expected = {m.registration_number for m in parsed if isinstance(m, Event)}
    stored = {r[0] for r in conn.execute("SELECT registration_number FROM companies").fetchall()}
    assert stored == expected and len(stored) > 1500
    rejects = sum(isinstance(m, Rejected) for m in parsed)
    assert conn.execute("SELECT coalesce(sum(seen_count), 0) FROM rejected_events").fetchone()[0] == rejects
    assert rejects + sum(isinstance(m, Event) for m in parsed) == len(lines)


# ---- 6. poison messages ------------------------------------------------------------------------------------------


def test_poison_is_stored_with_reason_and_identical_poison_is_counted(conn, embedder):
    bodies = [b'{"type": "company-upserted", "registrationNumber": "SYN-1", "updatedAt": "2026-08-1', b"null",
              b"null", json.dumps({"type": "company-upserted", "data": {}}).encode(),
              json.dumps({"type": "company-upserted", "registrationNumber": 119}).encode()]  # fmt: skip
    batch = [parse_message(f"m{i}", b) for i, b in enumerate(bodies)]
    assert all(isinstance(m, Rejected) for m in batch)
    process_batch(conn, embedder, batch)
    process_batch(conn, embedder, [parse_message("again", b"null")])  # a redelivery in a later batch
    rows = dict(
        conn.execute("SELECT reason, sum(seen_count) FROM rejected_events GROUP BY reason").fetchall()
    )
    assert rows == {
        "invalid_json": 1,
        "not_an_object": 3,
        "missing_registration_number": 1,
        "invalid_registration_number": 1,
    }
    assert (
        conn.execute("SELECT count(*) FROM rejected_events WHERE reason = 'not_an_object'").fetchone()[0] == 1
    )


# ---- 7. struck off -----------------------------------------------------------------------------------------------


def struck(ts: str) -> Event:
    return make_event(REG, ts, kind="company-struck-off")


def test_struck_off_sets_status_and_keeps_data(conn, embedder):
    run(conn, embedder, make_event(REG, "2026-01-01T00:00:00+08:00", companyName="Acme", industry="Retail"))
    run(conn, embedder, struck("2026-02-01T00:00:00+08:00"))
    row = company(conn)
    assert (row["status"], row["company_name"], row["industry"]) == ("struck_off", "Acme", "Retail")


def test_late_older_upsert_cannot_resurrect(conn, embedder):
    run(conn, embedder, struck("2026-02-01T00:00:00+08:00"))
    run(conn, embedder, make_event(REG, "2026-01-01T00:00:00+08:00", companyName="Acme"))
    assert company(conn)["status"] == "struck_off"


def test_newer_upsert_reactivates(conn, embedder):
    run(conn, embedder, struck("2026-02-01T00:00:00+08:00"))
    run(conn, embedder, make_event(REG, "2026-03-01T00:00:00+08:00", companyName="Acme"))
    assert company(conn)["status"] == "active"


def test_dataless_upsert_does_not_change_status(conn, embedder):
    run(conn, embedder, struck("2026-02-01T00:00:00+08:00"))
    dataless = parse_message(None, b'{"type": "company-upserted", "registrationNumber": "SYN-000001", '
                                   b'"updatedAt": "2026-03-01T00:00:00+08:00"}')  # fmt: skip
    assert isinstance(dataless, Event) and not dataless.has_data
    run(conn, embedder, dataless)
    assert company(conn)["status"] == "struck_off"


# ---- 8. embeddings -----------------------------------------------------------------------------------------------


def test_identical_text_is_embedded_once(conn, embedder):
    text = "Boutique hotel in Ipoh"
    run(
        conn,
        embedder,
        *(make_event(f"SYN-{i:06d}", "2026-01-01T00:00:00+08:00", natureOfBusiness=text) for i in range(5)),
    )
    assert embedder.texts == [text]
    # a later batch with the same text on another company reuses the stored vector
    run(conn, embedder, make_event("SYN-000099", "2026-01-01T00:00:00+08:00", natureOfBusiness=text))
    assert embedder.texts == [text]
    assert conn.execute("SELECT count(*) FROM companies WHERE embedding IS NOT NULL").fetchone()[0] == 6


def test_unchanged_text_is_not_reembedded_and_changed_text_is(conn, embedder):
    run(conn, embedder, make_event(REG, "2026-01-01T00:00:00+08:00", natureOfBusiness="Rubber gloves"))
    before = company(conn)["embedding"].to_list()
    run(
        conn,
        embedder,
        make_event(REG, "2026-02-01T00:00:00+08:00", natureOfBusiness="Rubber gloves", state="Perak"),
    )
    assert embedder.texts == ["Rubber gloves"]
    run(conn, embedder, make_event(REG, "2026-03-01T00:00:00+08:00", natureOfBusiness="Palm oil milling"))
    assert embedder.texts == ["Rubber gloves", "Palm oil milling"]
    assert company(conn)["embedding"].to_list() != before


def test_changed_model_name_reembeds(conn, embedder):
    run(conn, embedder, make_event(REG, "2026-01-01T00:00:00+08:00", natureOfBusiness="Rubber gloves"))
    other = CountingEmbedder(name="fake-2")
    run(
        conn,
        other,
        make_event(REG, "2026-01-01T00:00:00+08:00", natureOfBusiness="Rubber gloves", state="Perak"),
    )
    assert other.texts == ["Rubber gloves"] and company(conn)["embedding_model"] == "fake-2"


def test_fallback_text_until_a_real_description_arrives(conn, embedder):
    run(conn, embedder, make_event(REG, "2026-01-01T00:00:00+08:00", companyName="Acme", industry="Retail"))
    row = company(conn)
    assert (row["embedding_status"], row["embedding_text"]) == ("fallback", "Retail. Acme")
    run(conn, embedder, make_event(REG, "2026-02-01T00:00:00+08:00", natureOfBusiness="Sells shoes"))
    row = company(conn)
    assert (row["embedding_status"], row["embedding_text"]) == ("ok", "Sells shoes")


def test_no_description_and_no_industry_means_no_embedding(conn, embedder):
    run(conn, embedder, make_event(REG, "2026-01-01T00:00:00+08:00", companyName="Name Only"))
    row = company(conn)
    assert row["embedding"] is None and row["embedding_status"] == "missing" and embedder.texts == []


# ---- 9. failure isolation ----------------------------------------------------------------------------------------


def test_embedder_failure_on_one_text_rejects_only_that_message(conn):
    bad = CountingEmbedder(fail_on="POISON TEXT")
    good_event = make_event("SYN-000001", "2026-01-01T00:00:00+08:00", natureOfBusiness="Fine text")
    bad_event = make_event("SYN-000002", "2026-01-01T00:00:00+08:00", natureOfBusiness="POISON TEXT")
    result = process_batch_isolated(conn, bad, [good_event, bad_event])
    stored = {r[0] for r in conn.execute("SELECT registration_number FROM companies").fetchall()}
    assert stored == {"SYN-000001"}
    assert conn.execute("SELECT reason FROM rejected_events").fetchall() == [("processing_error",)]
    assert result.rejected == 1
    # the failed attempt left no half-written event behind
    assert (
        conn.execute(
            "SELECT count(*) FROM company_events WHERE registration_number = 'SYN-000002'"
        ).fetchone()[0]
        == 0
    )


def test_closed_connection_raises_operational_error_not_a_reject(conn, embedder):
    event = make_event(REG, "2026-01-01T00:00:00+08:00", natureOfBusiness="x")
    conn.close()
    with pytest.raises(psycopg.OperationalError):
        process_batch_isolated(conn, embedder, [event])
