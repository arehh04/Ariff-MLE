# Prompts

Use one prompt per session (new chat / fresh agent), in order. Put `AGENTS.md` in the repo root first; agents that
read it will follow it, otherwise start each session with: "Read AGENTS.md and docs/ first."

After every prompt: read the diff, run the checks, ask the agent to explain anything you could not explain yourself,
commit. If an agent proposes something not in the docs, ask for the trade-offs before accepting.

| # | File | Output |
|---|---|---|
| 00 | `00-orientation.md` | agent proves it understands the task; no code |
| 01 | `01-setup-and-risk-check.md` | stack running, dependencies installed |
| 02 | `02-profile-the-feed.md` | `scripts/profile_feed.py` and real numbers |
| 03 | `03-normalizer.md` | pure parsing/normalising + unit tests |
| 04 | `04-schema-and-write-path.md` | migration, projection, embeddings reuse, integration tests |
| 05 | `05-embedder.md` | fastembed + hashing placeholder |
| 06 | `06-consumer.md` | RabbitMQ consumer, batching, ack-after-commit |
| 07 | `07-api.md` | FastAPI service and tests |
| 08 | `08-proofs-and-reconcile.md` | replay, shuffle, concurrency, reconcile |
| 09 | `09-docker-and-ci.md` | optional: Dockerfile, GitHub Actions |
| 10 | `10-deep-dive.md` | your one-hour deep dive |
| 11 | `11-design-and-readme.md` | DESIGN.md and README from real results |
| 12 | `12-final-review.md` | adversarial review before submission |
