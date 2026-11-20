-- REFERENCE schema from the planning phase. Type it in yourself, or ask the agent to adapt it and explain every table.
-- Schema for the company recommendation service. Idempotent: safe to run on every start.
CREATE EXTENSION IF NOT EXISTS vector;

-- Append-only log of every identified event (normalised). The source of truth:
-- `companies` is a projection folded from this table, which is what makes the
-- final state independent of arrival order and safe to replay.
CREATE TABLE IF NOT EXISTS company_events (
    registration_number text        NOT NULL,
    version_hash        text        NOT NULL,   -- sha256 of the normalised event content
    message_id          text,                   -- AMQP message_id of the first delivery (audit only)
    kind                text        NOT NULL CHECK (kind IN ('upsert', 'struck_off')),
    source_updated_at   timestamptz NOT NULL,   -- 'epoch' sentinel when the feed gave none
    updated_at_missing  boolean     NOT NULL DEFAULT false,
    has_data            boolean     NOT NULL,   -- false for upserts without a usable `data` object
    company_name        text,
    nature_of_business  text,
    state               text,
    industry            text,
    source              text,
    warnings            text[]      NOT NULL DEFAULT '{}',
    received_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (registration_number, version_hash)
);

-- Current state of each company (projection of company_events + embedding).
CREATE TABLE IF NOT EXISTS companies (
    registration_number text PRIMARY KEY,       -- normalised: strip().upper()
    company_name        text,
    nature_of_business  text,                   -- cleaned description as received
    state               text,                   -- canonical title case, NOT trusted (conflicts with descriptions)
    industry            text,
    source              text,
    status              text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'struck_off')),
    source_updated_at   timestamptz NOT NULL,   -- newest event time seen
    updated_at_missing  boolean     NOT NULL DEFAULT false,
    embedding_text      text,                   -- exactly what was embedded
    embedding_text_hash text,
    embedding           vector(384),
    embedding_model     text,
    embedding_status    text        NOT NULL DEFAULT 'missing'
        CHECK (embedding_status IN ('ok', 'fallback', 'missing')),
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    last_applied_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS companies_embedding_hnsw
    ON companies USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS companies_embedding_text_hash_idx ON companies (embedding_text_hash);
CREATE INDEX IF NOT EXISTS companies_state_idx ON companies (state);
CREATE INDEX IF NOT EXISTS companies_industry_idx ON companies (lower(industry));

-- Poison messages: no identity or unparseable. Kept raw so nothing is silently lost.
CREATE TABLE IF NOT EXISTS rejected_events (
    id          bigserial PRIMARY KEY,
    message_id  text,
    reason      text        NOT NULL,
    raw         text        NOT NULL,
    seen_count  integer     NOT NULL DEFAULT 1,  -- redeliveries of identical poison bump this
    received_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);
-- Identical poison messages (replays, repeated `null` lines) collapse into one counted row.
CREATE UNIQUE INDEX IF NOT EXISTS rejected_events_dedupe
    ON rejected_events (reason, md5(raw));
