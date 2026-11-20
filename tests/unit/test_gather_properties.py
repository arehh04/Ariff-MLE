import asyncio

import pytest

from mle.config import Settings
from mle.consumer import _gather


@pytest.mark.parametrize(
    ("n", "size"),
    [(1, 1), (5, 1), (5, 5), (5, 3), (100, 100), (101, 100), (250, 100), (7, 2), (64, 8), (3, 50)],
)
async def test_batches_preserve_order_cover_everything_and_respect_the_size(n, size):
    settings = Settings(batch_size=size, batch_wait_seconds=0.01)
    inbox: asyncio.Queue = asyncio.Queue()
    for i in range(n):
        inbox.put_nowait(i)
    batches = []
    while not inbox.empty():
        batches.append(await _gather(inbox, settings, inbox.get_nowait()))
    assert [x for b in batches for x in b] == list(
        range(n)
    )  # nothing lost, nothing reordered, nothing repeated
    assert all(1 <= len(b) <= size for b in batches)
    assert all(len(b) == size for b in batches[:-1])  # only the last batch may be short


@pytest.mark.parametrize("wait", [0.0, 0.01, 0.05])
async def test_zero_or_short_wait_never_hangs(wait):
    batch = await asyncio.wait_for(
        _gather(asyncio.Queue(), Settings(batch_size=10, batch_wait_seconds=wait), "x"), 1
    )
    assert batch == ["x"]
