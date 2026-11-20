"""Edge cases of the write path and the schema."""

import dataclasses
import json
import threading

import psycopg
import pytest

from mle import db
from mle.normalize import parse_message
from mle.repository import BatchResult, process_batch, process_batch_isolated
from tests.integration.conftest import CountingEmbedder, make_event

TS = "2026-01-01T00:00:00+08:00"


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_empty_batch_is_a_no_op(conn, embedder):
    assert process_batch(conn, embedder, []) == BatchResult()
    assert one(conn, "SELECT count(*) FROM company_events") == 0


def test_duplicate_inside_one_batch_is_counted_once(conn, embedder):
    e = make_event("SYN-000001", TS, companyName="A", natureOfBusiness="x")
    result = process_batch(conn, embedder, [e, e, e])
    assert (result.events_new, result.events_duplicate) == (1, 2)


def test_redelivery_in_a_later_batch_is_a_duplicate_and_does_not_embed(conn, embedder):
    e = make_event("SYN-000001", TS, companyName="A", natureOfBusiness="x")
    process_batch(conn, embedder, [e])
    again = process_batch(conn, embedder, [e])
    assert (again.events_new, again.events_duplicate, again.embedded) == (0, 1, 0)


def test_mixed_batch_reports_each_kind(conn, embedder):
    batch = [make_event("SYN-000001", TS, companyName="A", natureOfBusiness="x"), parse_message(None, b"null"),
             make_event("SYN-000002", TS, companyName="B", natureOfBusiness="y"), parse_message(None, b"{broken")]  # fmt: skip
    r = process_batch(conn, embedder, batch)
    assert (r.events_new, r.rejected, r.embedded) == (2, 2, 2)


def test_rejects_only_batch_creates_no_company(conn, embedder):
    process_batch(conn, embedder, [parse_message(None, b"null")])
    assert (
        one(conn, "SELECT count(*) FROM companies") == 0
        and one(conn, "SELECT count(*) FROM rejected_events") == 1
    )


def test_nul_bytes_are_stripped_before_storage(conn, embedder):
    body = json.dumps({"type": "company-upserted", "registrationNumber": "SYN-000001", "updatedAt": TS,
                       "data": {"companyName": "Ac\u0000me", "natureOfBusiness": "ho\u0000tel"}}).encode()  # fmt: skip
    process_batch(conn, embedder, [parse_message(None, body)])
    assert one(conn, "SELECT company_name FROM companies") == "Acme"
    assert one(conn, "SELECT nature_of_business FROM companies") == "hotel"


def test_nul_in_a_poison_message_is_stored_without_it(conn, embedder):
    process_batch(conn, embedder, [parse_message(None, b'{"a": "x\x00y"')])
    assert "\x00" not in one(conn, "SELECT raw FROM rejected_events")


def test_over_cap_description_is_stored_capped_and_flagged(conn, embedder):
    long = "Boutique hotel in Ipoh with a very long marketing description " * 30
    process_batch(conn, embedder, [make_event("SYN-000001", TS, natureOfBusiness=long)])
    assert len(one(conn, "SELECT nature_of_business FROM companies")) <= 512
    assert "description_over_cap" in one(conn, "SELECT warnings FROM company_events")


def test_missing_timestamp_is_flagged_in_the_event_log(conn, embedder):
    process_batch(conn, embedder, [make_event("SYN-000001", None, companyName="A")])
    assert "updated_at_missing" in one(conn, "SELECT warnings FROM company_events")
    assert one(conn, "SELECT updated_at_missing FROM companies") is True


@pytest.mark.parametrize("text", ["Hotel butik di Ipoh", "Perniagaan dalam talian produk bayi", "日本語の説明", "Café résumé naïve",
                                  "emoji 🙂 hotel"])  # fmt: skip
def test_unicode_description_round_trips(conn, embedder, text):
    process_batch(conn, embedder, [make_event("SYN-000001", TS, natureOfBusiness=text)])
    assert one(conn, "SELECT nature_of_business FROM companies") == text


def test_three_hundred_companies_in_one_batch(conn, embedder):
    events = [
        make_event(f"SYN-{i:06d}", TS, companyName=f"C{i}", natureOfBusiness=f"text {i % 30}")
        for i in range(300)
    ]
    r = process_batch(conn, embedder, events)
    assert (r.events_new, r.embedded) == (300, 30)  # 30 distinct texts embedded once each
    assert one(conn, "SELECT count(*) FROM companies") == 300


def test_registration_number_is_stored_normalised(conn, embedder):
    process_batch(conn, embedder, [make_event(" syn-000007 ", TS, companyName="A")])
    assert one(conn, "SELECT registration_number FROM companies") == "SYN-000007"
    assert one(conn, "SELECT registration_number FROM company_events") == "SYN-000007"


def test_state_is_stored_in_canonical_title_case(conn, embedder):
    process_batch(conn, embedder, [make_event("SYN-000001", TS, state="NEGERI SEMBILAN")])
    assert one(conn, "SELECT state FROM companies") == "Negeri Sembilan"


def test_source_updated_at_is_the_newest_event_time(conn, embedder):
    for ts in ("2026-03-01T00:00:00+08:00", "2026-01-01T00:00:00+08:00", "2026-02-01T00:00:00+08:00"):
        process_batch(conn, embedder, [make_event("SYN-000001", ts, companyName="A")])
    assert one(conn, "SELECT source_updated_at = '2026-02-28T16:00:00Z'::timestamptz FROM companies")


def test_first_seen_is_kept_and_last_applied_advances(conn, embedder):
    process_batch(conn, embedder, [make_event("SYN-000001", TS, companyName="A")])
    first, applied = conn.execute("SELECT first_seen_at, last_applied_at FROM companies").fetchone()
    process_batch(conn, embedder, [make_event("SYN-000001", "2026-02-01T00:00:00+08:00", companyName="B")])
    first2, applied2 = conn.execute("SELECT first_seen_at, last_applied_at FROM companies").fetchone()
    assert first2 == first and applied2 > applied


def test_model_change_reembeds_every_company_once_per_distinct_text(conn):
    events = [make_event(f"SYN-{i:06d}", TS, natureOfBusiness=f"text {i % 4}") for i in range(20)]
    process_batch(conn, CountingEmbedder("m1"), events)
    other = CountingEmbedder("m2")
    process_batch(
        conn,
        other,
        [make_event(f"SYN-{i:06d}", "2026-02-01T00:00:00+08:00", state="Perak") for i in range(20)],
    )
    assert sorted(other.texts) == [f"text {i}" for i in range(4)]
    assert one(conn, "SELECT count(*) FROM companies WHERE embedding_model = 'm2'") == 20


def test_same_content_from_different_sources_keeps_the_smallest_source_in_any_order(conn, embedder):
    def with_source(source):
        payload = {"type": "company-upserted", "registrationNumber": "SYN-000001", "updatedAt": TS, "source": source,
                   "data": {"companyName": "A"}}  # fmt: skip
        return parse_message(None, json.dumps(payload).encode())

    for order in (["manual-entry", "ssm-batch", "partner-api"], ["partner-api", "manual-entry", "ssm-batch"]):
        conn.execute("TRUNCATE company_events, companies")
        for s in order:
            process_batch(conn, embedder, [with_source(s)])
        assert one(conn, "SELECT source FROM companies") == "manual-entry"
        assert one(conn, "SELECT count(*) FROM company_events") == 1


# ---- failure isolation ---------------------------------------------------------------------------------------------


class WrongSizeEmbedder(CountingEmbedder):
    def embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_wrong_vector_size_is_a_data_error_rejected_as_processing_error(conn):
    good = make_event("SYN-000001", TS, companyName="A")  # no text -> no embedding needed
    bad = make_event("SYN-000002", TS, natureOfBusiness="needs a vector")
    process_batch_isolated(conn, WrongSizeEmbedder(), [good, bad])
    assert one(conn, "SELECT count(*) FROM companies") == 1
    assert one(conn, "SELECT reason FROM rejected_events") == "processing_error"


def test_one_bad_message_among_many_only_loses_that_one(conn):
    embedder = CountingEmbedder(fail_on="POISON")
    events = [
        make_event(f"SYN-{i:06d}", TS, natureOfBusiness="POISON" if i == 7 else "fine") for i in range(15)
    ]
    process_batch_isolated(conn, embedder, events)
    assert (
        one(conn, "SELECT count(*) FROM companies") == 14
        and one(conn, "SELECT count(*) FROM rejected_events") == 1
    )


def test_a_plain_value_error_is_a_bug_and_propagates_instead_of_rejecting(conn):
    class Buggy(CountingEmbedder):
        def embed(self, texts):
            raise ValueError("bug in our code")

    with pytest.raises(ValueError, match="bug in our code"):
        process_batch_isolated(conn, Buggy(), [make_event("SYN-000001", TS, natureOfBusiness="x")])
    assert one(conn, "SELECT count(*) FROM rejected_events") == 0  # not hidden in the reject table


def test_a_failed_batch_leaves_nothing_behind(conn):
    events = [
        make_event(f"SYN-{i:06d}", TS, natureOfBusiness="POISON" if i == 3 else "fine") for i in range(5)
    ]
    with pytest.raises(ValueError):
        process_batch(conn, CountingEmbedder(fail_on="POISON"), events)
    assert (
        one(conn, "SELECT count(*) FROM company_events") == 0
        and one(conn, "SELECT count(*) FROM companies") == 0
    )


# ---- schema and migrations ----------------------------------------------------------------------------------------


def test_migrate_is_idempotent(test_dsn):
    assert db.migrate(test_dsn) == db.migrate(test_dsn) == ["001_init.sql"]


def test_migrate_from_several_processes_at_once(test_dsn):
    errors = []

    def run():
        try:
            db.migrate(test_dsn)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []


def test_vector_extension_and_hnsw_index_exist(conn):
    assert one(conn, "SELECT count(*) FROM pg_extension WHERE extname = 'vector'") == 1
    assert "hnsw" in one(conn, "SELECT indexdef FROM pg_indexes WHERE indexname = 'companies_embedding_hnsw'")


def seed_company(conn):
    conn.execute("INSERT INTO companies (registration_number, source_updated_at) VALUES ('SYN-X', now())")


def test_event_kind_is_checked(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO company_events (registration_number, version_hash, kind, source_updated_at, has_data) "
            "VALUES ('SYN-X', 'h', 'deleted', now(), false)"
        )


def test_company_status_is_checked(conn):
    seed_company(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE companies SET status = 'gone'")


def test_embedding_status_is_checked(conn):
    seed_company(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE companies SET embedding_status = 'maybe'")


def test_the_vector_column_rejects_the_wrong_dimension(conn):
    from pgvector import Vector

    conn.execute("INSERT INTO companies (registration_number, source_updated_at) VALUES ('SYN-X', now())")
    with pytest.raises(psycopg.errors.DataException):
        conn.execute("UPDATE companies SET embedding = %s", (Vector([0.1, 0.2, 0.3]),))


def test_event_primary_key_blocks_a_second_identical_row(conn):
    sql = ("INSERT INTO company_events (registration_number, version_hash, kind, source_updated_at, has_data) "
           "VALUES ('SYN-X', 'same', 'upsert', now(), false)")  # fmt: skip
    conn.execute(sql)
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(sql)


def test_rejected_events_collapse_identical_poison_by_database_constraint(conn):
    sql = "INSERT INTO rejected_events (reason, raw) VALUES ('x', 'same raw')"
    conn.execute(sql)
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(sql)


def test_text_that_cannot_be_encoded_rejects_only_that_message(conn, embedder):
    """A lone surrogate is not a psycopg.DataError (it is a UnicodeEncodeError); it must still not sink the batch."""
    good = make_event("SYN-000001", TS, companyName="Fine", natureOfBusiness="Fine text")
    bad = dataclasses.replace(
        make_event("SYN-000002", TS, natureOfBusiness="x"), company_name="bad \ud800 name"
    )
    result = process_batch_isolated(conn, embedder, [good, bad])
    assert {r[0] for r in conn.execute("SELECT registration_number FROM companies").fetchall()} == {
        "SYN-000001"
    }
    assert conn.execute("SELECT reason FROM rejected_events").fetchall() == [("processing_error",)]
    assert result.rejected == 1
