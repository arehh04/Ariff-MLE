# DESIGN

> Draft skeleton. Every number below must come from a command run in this repo; until then it stays `TODO`.

## 1. Overview
TODO: one paragraph + flow diagram (see `docs/03-architecture.md`).

| Requirement | Mechanism |
|---|---|
| Latest info wins | TODO (projection ordered by source `updatedAt`, not arrival) |
| No lost companies | TODO (append-only event log, reject table, ack after commit) |
| Fast | TODO (pgvector HNSW, vector passed as parameter) |

## 2. What the feed contains
TODO: table from `scripts/profile_feed.py` (problem, count, handling). Note anything not in `docs/02-feed-findings.md`.

## 3. Decisions
| Decision | Choice | Why | Rejected alternative |
|---|---|---|---|
| TODO | | | |

## 4. Assumptions
TODO: numbered list (see `docs/01-requirements.md`; items reserved for the human are decided there).

## 5. Results (pasted from real runs)
TODO: reconcile output, test counts and mutation checks, `EXPLAIN (ANALYZE)`, p50/p95/p99 with machine description.

## 6. Deep dive (<= 1 hour)
**Recommendation quality: does "similar" find the same business type, in English and in Malay?**

The feed has only 172 distinct descriptions for about 2,000 companies, and every business type appears in English and
Malay (`docs/02-feed-findings.md`, row 16). So an English-only model should under-match Malay. I measured it with
`scripts/eval_quality.py` (`uv run python -m scripts.eval_quality`).

*Method.* 42 business types come from keyword rules (an English and a Malay pattern per type). They use no model and not
the feed's coarse `industry` field; the pattern that matched also gives the language. For each description I rank every
other distinct description by cosine similarity (identical text is never a candidate) and score the top 10. Queries are
weighted by how many companies carry the description. `P@10` is the share of the top 10 with the same type. It cannot
reach 1.0 when a type has fewer than 10 other texts, so `capped` divides by `min(10, same-type texts)` instead. "masked"
replaces city names with `CITY` so a shared word like "Ipoh" gives no free credit.

| model (dim 384) | text | P@10 | capped | English queries | Malay queries |
|---|---|---|---|---|---|
| hashing placeholder | masked | 0.157 | 0.279 | 0.267 | 0.298 |
| `BAAI/bge-small-en-v1.5` | masked | 0.185 | 0.347 | 0.410 | 0.245 |
| `paraphrase-multilingual-MiniLM-L12-v2` | masked | 0.275 | **0.990** | 0.994 | 0.983 |

Unmasked (raw) text, capped / English / Malay: hashing 0.247 / 0.235 / 0.267, bge-small 0.353 / 0.426 / 0.234,
multilingual 0.987 / 0.992 / 0.979. Masking changes no conclusion.

*Reading it.* The English-only model is no better than the hashing baseline on Malay queries (0.245 vs 0.298) and finds
about 0.41 of the English ones. The multilingual model is near the ceiling for both languages. Its weakest queries are
the Malay parcel-delivery texts (capped 0.70), because that type has 17 same-type texts and 7 of the top 10 matched.
Recommendation: keep the multilingual model as the default (it also fits the 384-d `vector(384)` column).

*What this does not show.* 24 of the 42 types have exactly one same-type partner (the other-language twin) and 11
English-only types have none and are excluded, so the capped score is mostly "does the model find the translation".
The corpus is 172 short, template-like texts, so real descriptions will be harder. The labels were written by reading
these same descriptions, and business type is a proxy for "a human finds this similar". The metric also ignores latency,
index size, struck-off companies and companies with no description.

## 7. 10x volume
TODO.

## 8. What's left
TODO.

## 9. AI tools used
TODO: which tools, for what, what I wrote or changed myself, what I reviewed. Must be true; do not remove.
