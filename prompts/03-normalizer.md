# 03 Normaliser and parser (test first)

Context: `docs/01-requirements.md` (assumptions), `docs/02-feed-findings.md`, `docs/03-architecture.md` points 1-2 and 9.

Task: create `mle/normalize.py`, **pure functions only, no I/O**, with tests written first in `tests/unit/test_normalize.py`.
Show each test failing before you implement it.

Functions:
- `normalize_registration_number(value) -> str | None` (strings only, `strip().upper()`, empty -> None)
- `parse_timestamp(value) -> datetime | None` (UTC; ISO with offset, `Z`, no zone = +08:00, epoch ms int or digit string;
  bool/None/garbage/out-of-range -> None)
- `normalize_state(value)` (canonical Title Case for the 16 Malaysian states, a few aliases such as Penang, unknown kept Title-cased)
- `clean_text(value, max_chars=512)` (collapse whitespace, strip NUL, collapse a phrase repeated 3+ times, cap at a word boundary, None if empty)
- `embedding_source(nature, industry, name) -> (text | None, "ok" | "fallback" | "missing")`
- `parse_message(message_id, body: bytes) -> Event | Rejected` with reasons: `invalid_json`, `not_an_object`,
  `missing_registration_number`, `invalid_registration_number`, `unknown_event_type`. Event kinds: `upsert`, `struck_off`.
  Missing/garbled timestamp -> epoch sentinel + flag + warning. `data` missing or not an object -> keep the event, no fields, warning.
  `version_hash` = sha256 of the normalised content (kind, UTC time, flags, name, description, state, industry; not the source).

Tests must cover every row of the findings table, and that the same record in 3 timestamp formats gets the same hash.
Before you decide the null-handling or struck-off semantics in code, they are only represented as data here; do not
decide projection rules in this step.

Acceptance: `uv run pytest tests/unit -q` green, ruff clean; explain `_REPEATED_PHRASE`-style logic in plain words.
Commit: `feat: feed normalisation with tests`.
