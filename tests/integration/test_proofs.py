"""Order independence, idempotency and concurrency, proven against the independent oracle (test plan items 2, 3, 4)."""

import random
import threading
from pathlib import Path

import pytest

from mle import db
from mle.normalize import Event, Rejected, parse_message
from mle.repository import process_batch_isolated
from tests.integration.conftest import CountingEmbedder
from tests.oracle import RESET_SQL, assert_matches_oracle, events_of, random_messages, snapshot

FEED = Path(__file__).resolve().parents[2] / "data" / "feed.jsonl"
MODEL = "fake-1"


def feed_messages() -> list[Event | Rejected]:
    return [parse_message(None, line.encode()) for line in FEED.read_text(encoding="utf-8").splitlines()]


def load(conn, messages, batch: int, embedder=None) -> None:
    embedder = embedder or CountingEmbedder()
    for i in range(0, len(messages), batch):
        process_batch_isolated(conn, embedder, messages[i : i + batch])


@pytest.fixture(scope="module")
def baseline(test_dsn):
    """The feed loaded in order, once: the snapshot every other arrangement must reproduce."""
    conn = db.connect(test_dsn)
    conn.execute(RESET_SQL)
    load(conn, feed_messages(), 100)
    result = snapshot(conn)
    conn.close()
    return result


# ---- randomised histories vs the oracle (40 seeds x 3 shuffles) ------------------------------------------------


@pytest.mark.parametrize("seed", range(40))
def test_random_history_matches_the_oracle_in_any_order(conn, seed):
    messages = random_messages(seed)
    events = events_of(messages)
    rng = random.Random(seed + 1000)
    snapshots = []
    for _ in range(3):
        conn.execute(RESET_SQL)
        order = messages[:]
        rng.shuffle(order)
        order += rng.sample(order, k=len(order) // 4)  # redeliveries
        load(conn, order, batch=rng.choice([1, 3, 8, 1000]))
        assert_matches_oracle(conn, events, MODEL)
        snapshots.append(snapshot(conn))
    assert snapshots[0] == snapshots[1] == snapshots[2]


# ---- the real feed ----------------------------------------------------------------------------------------------


def test_feed_loaded_in_order_matches_the_oracle(conn, baseline):
    load(conn, feed_messages(), 100)
    assert_matches_oracle(conn, events_of(feed_messages()), MODEL)
    assert len(snapshot(conn)) == 2000
    assert snapshot(conn) == baseline


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
@pytest.mark.parametrize("batch", [7, 100, 500])
def test_shuffled_feed_gives_an_identical_database(conn, baseline, seed, batch):
    messages = feed_messages()
    random.Random(seed).shuffle(messages)
    load(conn, messages, batch)
    assert snapshot(conn) == baseline
    assert_matches_oracle(conn, events_of(messages), MODEL)


def test_reversed_feed_gives_an_identical_database(conn, baseline):
    load(conn, feed_messages()[::-1], 100)
    assert snapshot(conn) == baseline


@pytest.mark.parametrize("batch", [1, 50, 100])
def test_replaying_the_feed_changes_nothing(conn, baseline, batch):
    messages = feed_messages()
    load(conn, messages, 100)
    events_before = conn.execute("SELECT count(*) FROM company_events").fetchone()[0]
    reject_rows_before = conn.execute("SELECT count(*) FROM rejected_events").fetchone()[0]
    again = CountingEmbedder()
    load(conn, messages, batch, again)
    assert snapshot(conn) == baseline
    assert conn.execute("SELECT count(*) FROM company_events").fetchone()[0] == events_before == 2434
    assert conn.execute("SELECT count(*) FROM rejected_events").fetchone()[0] == reject_rows_before
    assert again.texts == []  # nothing new, so the model is never called


def test_replay_counts_redelivered_poison_but_adds_no_rows(conn):
    messages = feed_messages()
    load(conn, messages, 100)
    first = conn.execute("SELECT sum(seen_count) FROM rejected_events").fetchone()[0]
    load(conn, messages, 100)
    assert first == 21
    assert conn.execute("SELECT sum(seen_count) FROM rejected_events").fetchone()[0] == 2 * first


def test_feed_end_state_matches_the_findings_doc(conn):
    load(conn, feed_messages(), 100)
    q = lambda sql: conn.execute(sql).fetchone()[0]
    assert q("SELECT count(*) FROM companies") == 2000
    assert q("SELECT count(*) FROM companies WHERE status = 'struck_off'") == 8
    assert q("SELECT count(*) FROM company_events") == 2434
    assert q("SELECT sum(seen_count) FROM rejected_events") == 21
    assert q("SELECT count(*) FROM companies WHERE embedding IS NULL") == 4
    assert 30 <= q("SELECT count(*) FROM companies WHERE embedding_status = 'fallback'") <= 40


# ---- concurrency: separate connections, interleaved slices ------------------------------------------------------


def run_concurrently(test_dsn: str, messages, threads: int, batch: int) -> list[BaseException]:
    errors: list[BaseException] = []
    barrier = threading.Barrier(threads)
    embedder = CountingEmbedder()

    def worker(k: int) -> None:
        conn = db.connect(test_dsn)
        try:
            mine = messages[k::threads]  # interleaved: every thread touches the same companies
            barrier.wait()
            for i in range(0, len(mine), batch):
                process_batch_isolated(conn, embedder, mine[i : i + batch])
        except BaseException as exc:  # noqa: BLE001 - reported to the main thread
            errors.append(exc)
        finally:
            conn.close()

    pool = [threading.Thread(target=worker, args=(k,)) for k in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    return errors


@pytest.mark.parametrize(("threads", "batch"), [(4, 25), (4, 7), (8, 13)])
def test_concurrent_consumers_give_an_identical_database(conn, test_dsn, baseline, threads, batch):
    messages = feed_messages()
    assert run_concurrently(test_dsn, messages, threads, batch) == []  # no deadlock, no crash
    assert snapshot(conn) == baseline
    assert_matches_oracle(conn, events_of(messages), MODEL)


def test_concurrent_consumers_on_one_hot_company(conn, test_dsn):
    """Every thread writes events for the SAME company: the final row must still hold the newest value of each field."""
    from tests.integration.conftest import make_event

    events = [make_event("SYN-000001", f"2026-01-{day:02d}T00:00:00+08:00", companyName=f"Name {day}",
                         natureOfBusiness=f"text {day % 5}", state="Perak") for day in range(1, 29)]  # fmt: skip
    assert run_concurrently(test_dsn, events, threads=4, batch=2) == []
    assert_matches_oracle(conn, events, MODEL)
    assert conn.execute("SELECT company_name FROM companies").fetchone()[0] == "Name 28"
