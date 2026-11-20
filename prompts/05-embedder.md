# 05 Embedders

Context: `docs/03-architecture.md` (Embedding choice). Default model is decided: `bge-small-en-v1.5`, hashing for tests/fallback.

Task: `mle/config.py` (pydantic-settings, env prefix `MLE_`, compose defaults honouring `POSTGRES_PORT`/`RABBITMQ_PORT`) and
`mle/embedding.py` with a `Protocol` (`name`, `dim`, `embed(list[str]) -> list[list[float]]`), a `FastEmbedEmbedder` (lazy import,
checks the output dimension equals the schema's 384) and a `HashingEmbedder` placeholder (signed hashed unigrams + bigrams,
deterministic, L2-normalised, no `hash()` randomisation) selected by `MLE_EMBEDDER`. Unknown names fail loudly.
Unit tests: deterministic, unit length, related text closer than unrelated, empty text still valid, unknown name raises.

Acceptance: tests green; show real fastembed vs hashing cosine for ("Boutique hotel in Ipoh", "Hotel butik di Ipoh") and
("Boutique hotel in Ipoh", "Oil palm plantation") and tell me what that implies.
Commit: `feat: embedders`.
