import asyncio

from mle.config import Settings
from mle.consumer import _gather

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
