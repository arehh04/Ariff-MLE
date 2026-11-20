"""RabbitMQ consumer: batches messages, writes each batch in one Postgres transaction, THEN acks.

Failure policy: bad data is stored in `rejected_events` and acked (never requeued); a database outage is not acked,
the connection is rebuilt with back-off and the same batch is retried. A redelivered batch is harmless because the
event log ignores duplicates.
"""

import asyncio
import logging
import signal
from typing import Any

import aio_pika
import psycopg
from aio_pika.abc import AbstractIncomingMessage
from aio_pika.exceptions import AMQPError

from mle import db
from mle.config import Settings, get_settings
from mle.embedding import Embedder, make_embedder
from mle.normalize import parse_message
from mle.repository import BatchResult, process_batch_isolated
from producer.publish import QUEUE

log = logging.getLogger("consumer")
_MAX_BACKOFF = 30.0
_FIRST_BACKOFF = 1.0


class Stopping(Exception):
    """Shutdown was requested while the database was down. Nothing was acked; the broker redelivers."""


async def _gather(inbox: asyncio.Queue, settings: Settings, first: Any) -> list:
    """`first` plus whatever else arrives, up to `batch_size` messages or `batch_wait_seconds` from now."""
    batch = [first]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.batch_wait_seconds
    while len(batch) < settings.batch_size:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            batch.append(await asyncio.wait_for(inbox.get(), remaining))
        except TimeoutError:
            break
    return batch


async def _write_with_retry(
    conn: psycopg.Connection | None, settings: Settings, embedder: Embedder, parsed: list, stop: asyncio.Event
) -> tuple[psycopg.Connection, BatchResult]:
    """Retry the same batch until the database accepts it. Only infrastructure errors loop; nothing is acked here."""
    delay = _FIRST_BACKOFF
    while True:
        try:
            if conn is None or conn.closed:
                conn = await asyncio.to_thread(db.connect, settings.database_url)
            return conn, await asyncio.to_thread(process_batch_isolated, conn, embedder, parsed)
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            log.warning("database unavailable (%s); retrying in %.0fs, batch not acked", exc, delay)
            if conn is not None:
                await asyncio.to_thread(conn.close)
            conn = None
            try:
                await asyncio.wait_for(stop.wait(), delay)  # a sleep that Ctrl-C can interrupt
            except TimeoutError:
                delay = min(delay * 2, _MAX_BACKOFF)
            else:
                raise Stopping from exc


async def run(settings: Settings, embedder: Embedder, stop: asyncio.Event) -> BatchResult:
    await asyncio.to_thread(db.migrate, settings.database_url)
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=2 * settings.batch_size)
    queue = await channel.declare_queue(
        QUEUE, durable=True
    )  # exactly as the registry declares it: nothing else
    inbox: asyncio.Queue[AbstractIncomingMessage] = asyncio.Queue()
    consumer_tag = await queue.consume(inbox.put)  # manual acks: no_ack is False by default
    log.info("consuming %s (embedder %s, batch %d)", QUEUE, embedder.name, settings.batch_size)

    conn: psycopg.Connection | None = None
    totals = BatchResult()
    batches = 0
    try:
        while not stop.is_set():
            try:
                first = await asyncio.wait_for(inbox.get(), 1.0)  # wake up regularly to notice `stop`
            except TimeoutError:
                continue
            messages = await _gather(inbox, settings, first)
            parsed = [parse_message(m.message_id, m.body) for m in messages]
            try:
                conn, result = await _write_with_retry(conn, settings, embedder, parsed, stop)
            except Stopping:
                break  # unacked: these messages go back to the queue
            try:
                for m in messages:  # ack only now, after the transaction committed
                    await m.ack()
            except AMQPError as exc:
                log.warning("ack failed (%s): the broker will redeliver, duplicates are ignored", exc)
            totals += result
            batches += 1
            log.info("batch %d: %d msgs | new events %d, dup %d, rejected %d, embedded %d | totals: %s",
                     batches, len(messages), result.events_new, result.events_duplicate, result.rejected,
                     result.embedded, totals)  # fmt: skip
    finally:
        log.info("shutting down: unacked messages go back to the queue")
        await queue.cancel(consumer_tag)
        await connection.close()
        if conn is not None:
            conn.close()
    return totals


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()
    embedder = await asyncio.to_thread(make_embedder, settings)  # loads the model: do it before consuming
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)  # finish the current batch, ack it, then exit
    await run(settings, embedder, stop)


if __name__ == "__main__":
    asyncio.run(main())
