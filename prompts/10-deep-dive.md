# 10 Deep dive (max 1 hour)

I choose: recommendation quality. Context: `docs/02-feed-findings.md` row 16.

If recommendation quality:
- Build `scripts/eval_quality.py`: label the distinct descriptions with business types by keyword rules (English + Malay; independent
  of any model), then measure precision@10 of "same business type" **excluding identical-text matches**, weighted by company count,
  overall and for Malay vs English queries.
- Run it for: hashing placeholder, `BAAI/bge-small-en-v1.5`, and one multilingual 384-d model supported by fastembed (find it with
  `TextEmbedding.list_supported_models()`; check the dimension). Show the table, the weakest queries, and a recommendation.
- Do not claim a model is better without the numbers; say what the metric does not measure.
If another option: propose a plan with measurements I can actually run, and ask before building.

Acceptance: real outputs pasted; a short write-up for DESIGN.md section 6 with the table. Commit: `feat: deep dive eval`.
