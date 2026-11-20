"""An independent reference implementation of the projection, in plain Python (no SQL).

If the SQL in repository.py and this fold ever disagree, one of them is wrong; the tests use it as the oracle.
Rules (AGENTS.md "Decisions made"): newest non-null value per field wins, ordered by (source_updated_at, version_hash);
status comes from the newest event that strikes off or carries data; duplicates (same version_hash) collapse.
"""

import json
import random
from collections import defaultdict
from dataclasses import replace

from mle.normalize import Event, Rejected, embedding_source, parse_message

FIELDS = ("company_name", "nature_of_business", "state", "industry", "source")
SNAPSHOT_SQL = """
SELECT registration_number, company_name, nature_of_business, state, industry, source, status, source_updated_at,
       updated_at_missing, embedding_text, embedding_status, embedding_model, md5(embedding::text)
FROM companies ORDER BY registration_number
"""
RESET_SQL = "TRUNCATE company_events, companies, rejected_events RESTART IDENTITY"


def fold(events: list[Event], model: str) -> dict[str, tuple]:
    """Expected `companies` rows (same column order as SNAPSHOT_SQL minus the vector md5)."""
    by_reg: dict[str, dict[str, Event]] = defaultdict(dict)
    for e in events:
        kept = by_reg[e.registration_number].get(e.version_hash)
        if kept is not None:  # same content delivered twice: the log keeps the smallest non-null `source`
            sources = [s for s in (kept.source, e.source) if s is not None]
            e = replace(kept, source=min(sources) if sources else None)
        by_reg[e.registration_number][e.version_hash] = e
    expected = {}
    for reg, unique in by_reg.items():
        ordered = sorted(unique.values(), key=lambda e: (e.source_updated_at, e.version_hash), reverse=True)

        def newest(attr: str, ordered=ordered):
            return next((getattr(e, attr) for e in ordered if getattr(e, attr) is not None), None)

        deciding = [e for e in ordered if e.kind == "struck_off" or e.has_data]
        status = "struck_off" if deciding and deciding[0].kind == "struck_off" else "active"
        text, text_status = embedding_source(
            newest("nature_of_business"), newest("industry"), newest("company_name")
        )
        expected[reg] = (
            reg, *(newest(f) for f in FIELDS), status,
            max(e.source_updated_at for e in ordered), all(e.updated_at_missing for e in ordered),
            text, text_status if text else "missing", model if text else None,
        )  # fmt: skip
    return expected


def snapshot(conn) -> dict[str, tuple]:
    return {row[0]: row for row in conn.execute(SNAPSHOT_SQL).fetchall()}


def assert_matches_oracle(conn, events: list[Event], model: str) -> None:
    """Every stored company equals the oracle's fold, field by field, and nothing is missing or extra."""
    stored = {reg: row[:-1] for reg, row in snapshot(conn).items()}  # drop the vector md5
    expected = fold(events, model)
    assert set(stored) == set(expected), (
        f"missing {set(expected) - set(stored)}, extra {set(stored) - set(expected)}"
    )
    for reg, row in expected.items():
        assert stored[reg] == row, f"{reg}: stored {stored[reg]} != oracle {row}"


# ---- random event histories (seeded, reproducible) ----------------------------------------------------------------

_STAMPS = ["2026-01-01T00:00:00+08:00", "2025-12-31T16:00:00Z", "2026-02-01T10:00:00+08:00", "2026-03-05T12:30:00+08:00",
           1767225600000, "2026-01-01T00:00:00", None, "not a time"]  # fmt: skip
_NAMES = [None, "", "Acme", "Beta Sdn Bhd", "Gamma Trading"]
_NATURES = [None, "", "Boutique hotel in Ipoh", "Hotel butik di Ipoh", "Rubber gloves", "Palm oil plantation"]
_STATES = [None, "", "perak", "PERAK", "Selangor", "johor"]
_INDUSTRIES = [None, "", "Retail", "Healthcare"]
_JUNK = [
    b"null",
    b'{"type": "company-upserted"}',
    b"{broken",
    b'{"type": "x", "registrationNumber": "SYN-000001"}',
]


def random_messages(seed: int, companies: int = 8, per_company: int = 8) -> list[Event | Rejected]:
    """Small value pools on purpose: equal timestamps, repeated payloads and null fields are common."""
    rng = random.Random(seed)
    messages: list[Event | Rejected] = []
    for n in range(companies):
        reg = f"SYN-{n + 1:06d}"
        for _ in range(rng.randint(1, per_company)):
            raw_reg = rng.choice([reg, reg.lower(), f" {reg} "])
            payload: dict = {"registrationNumber": raw_reg}
            stamp = rng.choice(_STAMPS)
            if stamp is not None:
                payload["updatedAt"] = stamp
            roll = rng.random()
            if roll < 0.15:
                payload["type"] = "company-struck-off"
            else:
                payload["type"] = "company-upserted"
                if roll >= 0.30:  # else: an upsert with no `data` at all
                    payload["data"] = {
                        "companyName": rng.choice(_NAMES), "natureOfBusiness": rng.choice(_NATURES),
                        "state": rng.choice(_STATES), "industry": rng.choice(_INDUSTRIES),
                    }  # fmt: skip
            if rng.random() < 0.3:
                payload[rng.choice(["source", "ingestedBy"])] = rng.choice(
                    ["ssm-batch", "partner-api", "manual-entry"]
                )
            messages.append(parse_message(None, json.dumps(payload).encode()))
    messages += [parse_message(None, rng.choice(_JUNK)) for _ in range(rng.randint(0, 3))]
    return messages


def events_of(messages: list[Event | Rejected]) -> list[Event]:
    return [m for m in messages if isinstance(m, Event)]
