"""All SQL lives here. The write path: one batch of parsed messages -> one Postgres transaction.

Decisions made by the human (AGENTS.md "Decisions made"), implemented below:
- `company-struck-off` sets status 'struck_off' and keeps the company's data; a NEWER upsert that carries data
  reactivates it; an older late upsert cannot; an upsert without `data` never changes status.
- A newer null/empty field never erases an older value (nulls are filtered out per field in the projection).
- Timestamps without an offset were already resolved to +08:00 by the normaliser.

Order independence: `companies` is a pure function of the rows in `company_events`, so arrival order, redelivery and
replay cannot change the result.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Self

import psycopg
from pgvector import Vector

from mle.embedding import Embedder, UnembeddableText
from mle.normalize import Event, Rejected, embedding_source


@dataclass
class BatchResult:
    events_new: int = 0
    events_duplicate: int = 0
    rejected: int = 0
    embedded: int = 0  # texts actually sent to the model

    def __iadd__(self, other: BatchResult) -> Self:
        self.events_new += other.events_new
        self.events_duplicate += other.events_duplicate
        self.rejected += other.rejected
        self.embedded += other.embedded
        return self


_UPSERT_REJECT = """
INSERT INTO rejected_events (message_id, reason, raw, seen_count)
VALUES (%s, %s, %s, %s)
ON CONFLICT (reason, md5(raw)) DO UPDATE
SET seen_count = rejected_events.seen_count + EXCLUDED.seen_count, last_seen_at = now()
"""

_INSERT_EVENT = """
INSERT INTO company_events (registration_number, version_hash, message_id, kind, source_updated_at,
    updated_at_missing, has_data, company_name, nature_of_business, state, industry, source, warnings)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (registration_number, version_hash) DO UPDATE
SET source = LEAST(company_events.source, EXCLUDED.source)
WHERE company_events.source IS DISTINCT FROM LEAST(company_events.source, EXCLUDED.source)
RETURNING (xmax = 0)
"""
# `source` is not part of version_hash, so redeliveries of the same content may disagree on it. "First delivery wins"
# would make companies.source depend on arrival order; keeping the smallest non-null value does not (LEAST ignores
# NULL). The WHERE clause makes an unchanged duplicate write nothing. RETURNING (xmax = 0) is true only for a real insert.

# Fold the event log into one row per company. Every field: the value of the NEWEST event that has one.
# "Newest" = source_updated_at DESC, then version_hash DESC as a fixed tie-break, so equal timestamps resolve
# identically whatever order the events arrived in. FILTER drops NULLs first, so a newer null cannot erase a value.
_PROJECT = """
SELECT registration_number,
  (array_agg(company_name ORDER BY source_updated_at DESC, version_hash DESC)
      FILTER (WHERE company_name IS NOT NULL))[1] AS company_name,
  (array_agg(nature_of_business ORDER BY source_updated_at DESC, version_hash DESC)
      FILTER (WHERE nature_of_business IS NOT NULL))[1] AS nature_of_business,
  (array_agg(state ORDER BY source_updated_at DESC, version_hash DESC)
      FILTER (WHERE state IS NOT NULL))[1] AS state,
  (array_agg(industry ORDER BY source_updated_at DESC, version_hash DESC)
      FILTER (WHERE industry IS NOT NULL))[1] AS industry,
  (array_agg(source ORDER BY source_updated_at DESC, version_hash DESC)
      FILTER (WHERE source IS NOT NULL))[1] AS source,
  -- status: the newest event that either strikes the company off or carries data. Data-less upserts are skipped.
  (array_agg(kind ORDER BY source_updated_at DESC, version_hash DESC)
      FILTER (WHERE kind = 'struck_off' OR has_data))[1] AS last_status_kind,
  max(source_updated_at) AS source_updated_at,
  bool_and(updated_at_missing) AS updated_at_missing  -- true only if no event had a real time
FROM company_events
WHERE registration_number = ANY(%s)
GROUP BY registration_number
"""

_EXISTING_EMBEDDINGS = """
SELECT registration_number, embedding_text_hash, embedding_model, embedding
FROM companies WHERE registration_number = ANY(%s)
"""

_REUSABLE_VECTORS = """
SELECT DISTINCT ON (embedding_text_hash) embedding_text_hash, embedding
FROM companies
WHERE embedding_text_hash = ANY(%s) AND embedding_model = %s AND embedding IS NOT NULL
"""

_UPSERT_COMPANY = """
INSERT INTO companies (registration_number, company_name, nature_of_business, state, industry, source, status,
    source_updated_at, updated_at_missing, embedding_text, embedding_text_hash, embedding, embedding_model,
    embedding_status)
VALUES (%(registration_number)s, %(company_name)s, %(nature_of_business)s, %(state)s, %(industry)s, %(source)s,
    %(status)s, %(source_updated_at)s, %(updated_at_missing)s, %(embedding_text)s, %(embedding_text_hash)s,
    %(embedding)s, %(embedding_model)s, %(embedding_status)s)
ON CONFLICT (registration_number) DO UPDATE SET
    company_name = EXCLUDED.company_name, nature_of_business = EXCLUDED.nature_of_business,
    state = EXCLUDED.state, industry = EXCLUDED.industry, source = EXCLUDED.source, status = EXCLUDED.status,
    source_updated_at = EXCLUDED.source_updated_at, updated_at_missing = EXCLUDED.updated_at_missing,
    embedding_text = EXCLUDED.embedding_text, embedding_text_hash = EXCLUDED.embedding_text_hash,
    embedding = EXCLUDED.embedding, embedding_model = EXCLUDED.embedding_model,
    embedding_status = EXCLUDED.embedding_status, last_applied_at = now()
"""


def _as_vector(value: object) -> Vector:
    """pgvector hands back Vector objects on read; the embedder returns plain float sequences."""
    return value if isinstance(value, Vector) else Vector(value)  # type: ignore[arg-type]


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _record_rejects(conn: psycopg.Connection, rejects: list[Rejected]) -> None:
    # Identical raws are folded in Python and written in sorted order: concurrent consumers then lock the
    # same rows in the same order, so they cannot deadlock.
    counts = Counter((r.reason, r.raw) for r in rejects)
    first_message_id = {}
    for r in rejects:
        first_message_id.setdefault((r.reason, r.raw), r.message_id)
    for (reason, raw), n in sorted(counts.items()):
        conn.execute(_UPSERT_REJECT, (first_message_id[(reason, raw)], reason, raw, n))


def _insert_events(conn: psycopg.Connection, events: list[Event]) -> tuple[int, int]:
    new = 0
    for e in events:
        cur = conn.execute(
            _INSERT_EVENT,
            (e.registration_number, e.version_hash, e.message_id, e.kind, e.source_updated_at,
             e.updated_at_missing, e.has_data, e.company_name, e.nature_of_business, e.state, e.industry,
             e.source, list(e.warnings)),
        )  # fmt: skip
        row = cur.fetchone()  # None: a duplicate that changed nothing
        new += bool(row and row[0])
    return new, len(events) - new


def _plan_embeddings(
    conn: psycopg.Connection, embedder: Embedder, projected: list[dict]
) -> tuple[dict[str, Vector], int]:
    """Decide which companies need a vector. Returns {registration_number: Vector} for those whose row must be
    written with a (possibly reused) vector, plus how many texts were really embedded.

    Order of preference: unchanged text+model on the same row (keep) -> identical text already stored (reuse)
    -> embed, each distinct text once per batch.
    """
    regs = [p["registration_number"] for p in projected]
    existing = {
        row[0]: (row[1], row[2], row[3]) for row in conn.execute(_EXISTING_EMBEDDINGS, (regs,)).fetchall()
    }
    vectors: dict[str, Vector] = {}
    need: dict[str, list[str]] = {}  # text hash -> registration numbers waiting for that vector
    texts: dict[str, str] = {}
    for p in projected:
        text = p["embedding_text"]
        if text is None:
            continue
        digest = p["embedding_text_hash"] = _text_hash(text)
        old_hash, old_model, old_vector = existing.get(p["registration_number"], (None, None, None))
        if old_hash == digest and old_model == embedder.name and old_vector is not None:
            vectors[p["registration_number"]] = _as_vector(old_vector)
        else:
            need.setdefault(digest, []).append(p["registration_number"])
            texts[digest] = text
    if need:
        stored = conn.execute(_REUSABLE_VECTORS, (list(need), embedder.name)).fetchall()
        for digest, vector in stored:
            for reg in need.pop(digest):
                vectors[reg] = _as_vector(vector)
    embedded = 0
    if need:
        order = list(need)
        new_vectors = embedder.embed([texts[d] for d in order])
        if len(new_vectors) != len(order):
            raise RuntimeError("embedder returned a different number of vectors than texts")
        embedded = len(order)
        for digest, vector in zip(order, new_vectors, strict=True):
            for reg in need[digest]:
                vectors[reg] = _as_vector(vector)
    return vectors, embedded


def process_batch(
    conn: psycopg.Connection, embedder: Embedder, messages: Sequence[Event | Rejected]
) -> BatchResult:
    """Write one batch in ONE transaction. The caller acks the messages only after this returns.

    Steps: record rejects -> lock the batch's companies (sorted) -> append events (duplicates ignored) ->
    re-project the touched companies from the log -> embed what changed -> upsert `companies`.
    """
    rejects = [m for m in messages if isinstance(m, Rejected)]
    events = [m for m in messages if isinstance(m, Event)]
    result = BatchResult(rejected=len(rejects))
    with conn.transaction():
        if rejects:
            _record_rejects(conn, rejects)
        if not events:
            return result
        regs = sorted({e.registration_number for e in events})
        for (
            reg
        ) in regs:  # sorted + transaction-scoped: two consumers cannot deadlock or project on stale data
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (reg,))
        result.events_new, result.events_duplicate = _insert_events(conn, events)

        cur = conn.execute(_PROJECT, (regs,))
        columns = [c.name for c in cur.description]
        projected = []
        for row in cur.fetchall():
            p = dict(zip(columns, row, strict=True))
            p["status"] = "struck_off" if p.pop("last_status_kind") == "struck_off" else "active"
            p["embedding_text"], p["embedding_status"] = embedding_source(
                p["nature_of_business"], p["industry"], p["company_name"]
            )
            p["embedding_text_hash"] = None
            projected.append(p)

        vectors, result.embedded = _plan_embeddings(conn, embedder, projected)
        for p in projected:
            vector = vectors.get(p["registration_number"])
            p["embedding"] = vector
            p["embedding_model"] = embedder.name if vector is not None else None
            if vector is None:
                p["embedding_status"] = "missing"
            conn.execute(_UPSERT_COMPANY, p)
    return result


def process_batch_isolated(
    conn: psycopg.Connection, embedder: Embedder, messages: Sequence[Event | Rejected]
) -> BatchResult:
    """Like `process_batch`, but one bad message cannot sink the batch.

    Data-level errors (psycopg.DataError, or UnembeddableText from the embedder) -> retry message by message and store the
    offender in `rejected_events` as `processing_error`. Anything else (OperationalError, a dead connection, ...)
    propagates untouched so the consumer does not ack and retries with back-off.
    """
    try:
        return process_batch(conn, embedder, messages)
    except psycopg.DataError, UnembeddableText:
        pass  # the failed transaction was rolled back; fall through to one-by-one
    total = BatchResult()
    for message in messages:
        try:
            total += process_batch(conn, embedder, [message])
        except (psycopg.DataError, UnembeddableText) as exc:
            if isinstance(message, Rejected):
                raise
            raw = json.dumps({"error": f"{type(exc).__name__}: {exc}", "event": asdict(message)}, default=str)
            total += process_batch(conn, embedder, [Rejected(message.message_id, "processing_error", raw)])
    return total


# ---- read path (used by the API) -------------------------------------------------------------------------------

_GET_COMPANY = """
SELECT registration_number, company_name, nature_of_business, state, industry, status, source,
       source_updated_at, updated_at_missing, embedding_status, embedding_model, embedding
FROM companies WHERE registration_number = %s
"""

# The query vector arrives as a PARAMETER (fetched first by get_company), so the planner sees a constant and can use
# the HNSW index. Extra ORDER BY keys would stop it using the index, so the inner query orders by distance ONLY and
# takes the `pool` nearest rows; the outer query then breaks ties (the feed has many identical descriptions):
# same state first, then registration number. Safe while `pool` exceeds the biggest tie group (~43 in the feed).
_SIMILAR = """
SELECT registration_number, company_name, nature_of_business, state, industry, status, source_updated_at,
       updated_at_missing, 1 - distance AS score
FROM (
    SELECT registration_number, company_name, nature_of_business, state, industry, status, source_updated_at,
           updated_at_missing, embedding <=> %(q)s AS distance
    FROM companies
    WHERE embedding IS NOT NULL
      AND registration_number <> %(reg)s
      AND (%(include_struck_off)s OR status = 'active')
      AND (%(state)s::text IS NULL OR state = %(state)s)
      AND (%(industry)s::text IS NULL OR lower(industry) = lower(%(industry)s))
    ORDER BY embedding <=> %(q)s
    LIMIT %(pool)s
) nearest
ORDER BY distance, coalesce(state = %(own_state)s, false) DESC, registration_number
LIMIT %(limit)s
"""
_EF_SEARCH = 200  # pgvector returns at most this many rows per index scan, so a bigger pool would not help


def _dicts(cur: psycopg.Cursor) -> list[dict]:
    names = [c.name for c in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def get_company(conn: psycopg.Connection, registration_number: str) -> dict | None:
    rows = _dicts(conn.execute(_GET_COMPANY, (registration_number,)))
    return rows[0] if rows else None


def similar(
    conn: psycopg.Connection,
    company: dict,
    limit: int,
    state: str | None,
    industry: str | None,
    include_struck_off: bool,
) -> list[dict]:
    """Top `limit` companies closest to `company` by cosine distance. `company` comes from get_company()."""
    with conn.transaction():
        # Filters are applied after the index scan, so look at more candidates than the default 40 to still fill `limit`.
        conn.execute("SET LOCAL hnsw.ef_search = 200")  # keep equal to _EF_SEARCH
        params = {
            "q": company["embedding"], "reg": company["registration_number"], "own_state": company["state"],
            "include_struck_off": include_struck_off, "state": state, "industry": industry, "limit": limit,
            "pool": min(_EF_SEARCH, max(100, 4 * limit)),
        }  # fmt: skip
        return _dicts(conn.execute(_SIMILAR, params))
