# 06 Consumer

Context: `docs/03-architecture.md` points 6 and 8; `producer/publish.py` (read-only); `AGENTS.md` (queue declaration, ack rules).

Task: `mle/consumer.py` with aio-pika:
- `connect_robust`; declare the queue `durable=True` and NOTHING else; `set_qos(prefetch_count=2*batch)`; manual acks via
  `queue.consume(callback)` feeding an `asyncio.Queue`.
- `_gather(inbox, settings, first)` returning up to `batch_size` messages or whatever arrives within `batch_wait_seconds`.
- Run `process_batch_isolated` in `asyncio.to_thread`; **ack only after it returns**; on `psycopg.OperationalError/InterfaceError`:
  log, sleep, close, reconnect, retry the same batch (never ack). Graceful SIGINT/SIGTERM.
- Per-batch log line with counts and running totals. Apply the schema on start.
Unit tests for `_gather` (size cutoff, wait flush, lone message) with a plain `asyncio.Queue`.

Then manually: `just up`, `just consume` in one terminal, `just publish` in another; show the log, the RabbitMQ queue depth
reaching 0 with 0 unacked, and `select count(*) from companies` (expect 2000). Run `just publish` again and show nothing changes.
Kill the consumer with Ctrl-C mid-publish, restart it, and show the final counts are still right.

Acceptance: output of all of the above. Commit: `feat: rabbitmq consumer`.
