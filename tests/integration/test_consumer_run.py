"""consumer.run against a real RabbitMQ and Postgres: when does it ack, and what does a crash leave on the queue?

Each test uses its own queue (never the real `company-events`), declared durable with no arguments like the registry's.
"""

import asyncio
import json
import time
import uuid

import aio_pika
import psycopg
import pytest

from mle import consumer
from mle.config import Settings
from tests.integration.conftest import CountingEmbedder, service_unavailable

N = 5


@pytest.fixture
async def queue_name():
    name = f"test-company-events-{uuid.uuid4().hex[:8]}"
    try:
        connection = await aio_pika.connect_robust(Settings().rabbitmq_url, timeout=3)
    except (OSError, aio_pika.exceptions.AMQPError) as exc:
        service_unavailable(f"RabbitMQ not reachable (run `just up`): {exc}")
    async with connection:
        channel = await connection.channel()
        await channel.declare_queue(name, durable=True)
        for i in range(N):
            body = json.dumps({"type": "company-upserted", "registrationNumber": f"SYN-{i:06d}",
                               "updatedAt": "2026-01-01T00:00:00+08:00",
                               "data": {"companyName": f"Co {i}", "natureOfBusiness": f"Business {i}"}})  # fmt: skip
            await channel.default_exchange.publish(
                aio_pika.Message(body.encode(), message_id=f"m{i}"), routing_key=name
            )
        yield name
        await channel.queue_delete(name)


def settings_for(dsn: str) -> Settings:
    return Settings(database_url=dsn, batch_size=10, batch_wait_seconds=0.3)


def committed_events(dsn: str) -> int:
    with psycopg.connect(dsn) as connection:
        return connection.execute("SELECT count(*) FROM company_events").fetchone()[0]


async def messages_waiting(name: str, expected: int, timeout: float = 10.0) -> int:
    """Message count on the queue, polled because a closed connection returns its unacked messages asynchronously."""
    deadline = time.monotonic() + timeout
    async with await aio_pika.connect_robust(Settings().rabbitmq_url) as connection:
        channel = await connection.channel()
        while True:
            count = (await channel.declare_queue(name, passive=True)).declaration_result.message_count
            if count == expected or time.monotonic() > deadline:
                return count
            await asyncio.sleep(0.1)


async def test_every_ack_happens_after_the_batch_is_committed(conn, test_dsn, queue_name, monkeypatch):
    """At the moment of the k-th ack, at least k events must already be committed. Acking first would show fewer."""
    committed_at_ack: list[int] = []
    original_ack = aio_pika.IncomingMessage.ack

    async def spy(self, *args, **kwargs):
        committed_at_ack.append(committed_events(test_dsn))
        return await original_ack(self, *args, **kwargs)

    monkeypatch.setattr(aio_pika.IncomingMessage, "ack", spy)
    stop = asyncio.Event()
    task = asyncio.create_task(consumer.run(settings_for(test_dsn), CountingEmbedder(), stop, queue_name))
    deadline = time.monotonic() + 20
    while len(committed_at_ack) < N and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 10)
    assert len(committed_at_ack) == N
    assert all(seen >= k + 1 for k, seen in enumerate(committed_at_ack)), committed_at_ack
    assert committed_events(test_dsn) == N
    assert await messages_waiting(queue_name, 0) == 0  # everything acked, nothing left


async def test_a_crash_before_commit_leaves_every_message_on_the_queue(
    conn, test_dsn, queue_name, monkeypatch
):
    def explode(connection, embedder, parsed):
        raise RuntimeError("a bug in the write path")

    monkeypatch.setattr(consumer, "process_batch_isolated", explode)
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(
            consumer.run(settings_for(test_dsn), CountingEmbedder(), asyncio.Event(), queue_name), 20
        )
    assert committed_events(test_dsn) == 0
    assert await messages_waiting(queue_name, N) == N  # nothing was acked: the broker still holds all of them
