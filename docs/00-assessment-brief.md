# Assessment brief (summary of the template README, version 2026.11)

Source: https://github.com/OneCreditMY/mle-take-home (`mle-v2026.11`). This is a summary for agents and for me;
the template's own README is the authority if they ever differ.

## Why this exists
OneCredit (Malaysian credit bureau) hires an early-career ML Engineer (0-2 YOE) to run ML and LLM products in
production: FastAPI services, RabbitMQ, Docker, CI/CD, AWS, monitoring of latency/errors/drift/cost/output quality.
Everyone who submits gets written feedback.

## Scenario
A "Similar companies" panel on a company page of the OneCredit portal (card: name, registration number,
industry, state, short description). The registry publishes every company change to RabbitMQ queue
`company-events`; we build the consumer and the service from that feed.

```
producer -> RabbitMQ[company-events] -> consumer -> embed description -> Postgres + pgvector <- service <- portal
```

Registry notes: feed merges several upstream sources and is not validated; delivery is at-least-once; every message
has an AMQP `message_id`; `updatedAt` is the time of the change at the source; the registry owns the queue, declared
durable with no extra arguments; the format cannot change.

## Portal needs (the three requirements)
1. Recommendations reflect the latest information received about each company.
2. No lost companies: every company in the feed appears on the portal and in others' recommendations.
3. Fast enough to render while the page loads.

## Task
1. Consumer: read `company-events`, store each company in Postgres with a description embedding in pgvector.
2. Service: given a company, return its most similar companies with what the card needs; similarity runs in
   Postgres via pgvector. Matching on nature of business is enough for a first version.
3. `DESIGN.md`: decisions, assumptions, what changes at 10x volume, what's left.
4. Optional, max 1 hour of the 6: one deep dive (recommendation quality, latency at scale, production monitoring,
   or an LLM explanation design with no paid APIs), written up in `DESIGN.md`.
Free choices: schema, embedding model, tests, tooling, API, and what the portal gets on error. Our own README must
let them start the consumer and service. Where the scenario is open: make a call, write it down.

## Embeddings
Open-source, laptop CPU, no paid API. `BAAI/bge-small-en-v1.5` (384d) via fastembed or sentence-transformers, or a
declared placeholder such as a hashed bag-of-words vector.

## Provided files (do not modify)
`data/feed.jsonl` (2,518 messages, ~2,000 synthetic companies, duplicates), `producer/publish.py`,
`docker-compose.yml`, `Justfile`, `pyproject.toml`, `uv.lock`, `.python-version`, `ASSESSMENT_VERSION`,
`CHANGELOG.md`, `.github/ISSUE_TEMPLATE/`.
Pins: `pgvector/pgvector:0.8.7-pg16`, `rabbitmq:3.13.7-management`, Python `>=3.14,<3.15`. `message_id` is the
sha256 of the raw line. Ports overridable by `POSTGRES_PORT`, `RABBITMQ_PORT`, `RABBITMQ_UI_PORT`.

## Rules and logistics
- Time: 4-6 hours, within 7 days of creating the copy, stop at 6h; an honest "what's left" counts in our favour.
- Submit: public repo (copy of the template) with `DESIGN.md` at top level, then the Google Form from the template
  README with the repo link and résumé. Setup breakage -> public "Setup problem" issue (no solution or personal data in it).
- AI tools are allowed; list them and their use in `DESIGN.md`; I must be able to explain everything submitted.
- What they judge: does it work and keep working; how it was tested; reasoning in `DESIGN.md`; readable code.
- Data is synthetic (`SYN-` numbers). The repo is public: no secrets or personal data.
