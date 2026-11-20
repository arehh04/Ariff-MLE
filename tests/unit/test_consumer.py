import asyncio

import psycopg
import pytest

from mle import consumer
from mle.config import Settings
from mle.consumer import _gather
from mle.repository import BatchResult

FAST = Settings(batch_size=3, batch_wait_seconds=0.05)


async def test_size_cutoff_leaves_the_rest_in_the_queue():
    inbox: asyncio.Queue = asyncio.Queue()
    for i in range(1, 6):
        inbox.put_nowait(i)
    assert await _gather(inbox, FAST, first=0) == [0, 1, 2]
    assert inbox.qsize() == 3  # 3, 4, 5 wait for the next batch


async def test_wait_flushes_a_partial_batch():
    inbox: asyncio.Queue = asyncio.Queue()
    inbox.put_nowait("b")
    assert await _gather(inbox, FAST, first="a") == ["a", "b"]  # only 2 of 3 arrived, flushed after the wait


async def test_lone_message_is_returned_after_the_wait():
    start = asyncio.get_running_loop().time()
    assert await _gather(asyncio.Queue(), FAST, first="only") == ["only"]
    assert asyncio.get_running_loop().time() - start < 0.5  # waited ~batch_wait, did not hang


async def test_message_arriving_during_the_wait_is_included():
    inbox: asyncio.Queue = asyncio.Queue()
    asyncio.get_running_loop().call_later(0.01, inbox.put_nowait, "late")
    assert await _gather(inbox, FAST, first="a") == ["a", "late"]


# ---- outage handling: retry the SAME batch, never lose it, and let Ctrl-C through ------------------------------


class FakeConn:
    closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def outage(monkeypatch):
    """Patch the consumer so the 'database' fails `fail_times` times, then works. Returns the list of calls."""
    calls: list[list] = []

    def install(fail_times: int, error: Exception | None = None):
        error = error or psycopg.OperationalError("down")

        def flaky(conn, embedder, parsed):
            calls.append(list(parsed))
            if len(calls) <= fail_times:
                raise error
            return BatchResult(events_new=len(parsed))

        monkeypatch.setattr(consumer, "process_batch_isolated", flaky)
        monkeypatch.setattr(consumer.db, "connect", lambda url: FakeConn())
        monkeypatch.setattr(consumer, "_FIRST_BACKOFF", 0.01)
        return calls

    return install


async def test_outage_retries_the_same_batch_until_it_succeeds(outage):
    calls = outage(fail_times=2)
    _conn, result = await consumer._write_with_retry(None, Settings(), None, ["m1", "m2"], asyncio.Event())
    assert (
        calls == [["m1", "m2"]] * 3 and result.events_new == 2
    )  # failed twice, then the same batch went through


async def test_a_broken_connection_is_replaced_between_attempts(outage):
    outage(fail_times=1)
    first = FakeConn()
    conn, _ = await consumer._write_with_retry(first, Settings(), None, ["m"], asyncio.Event())
    assert first.closed and conn is not first


async def test_non_database_errors_are_not_retried(outage):
    calls = outage(fail_times=5, error=RuntimeError("a bug"))
    with pytest.raises(RuntimeError):
        await consumer._write_with_retry(None, Settings(), None, ["m"], asyncio.Event())
    assert len(calls) == 1  # a code bug must surface, not loop forever


async def test_stop_during_an_outage_gives_up_instead_of_hanging(outage, monkeypatch):
    calls = outage(fail_times=999)
    monkeypatch.setattr(consumer, "_FIRST_BACKOFF", 30.0)  # would wait half a minute if stop were ignored
    stop = asyncio.Event()
    asyncio.get_running_loop().call_later(0.05, stop.set)
    with pytest.raises(consumer.Stopping):
        await asyncio.wait_for(consumer._write_with_retry(None, Settings(), None, ["m"], stop), 2)
    assert len(calls) == 1
