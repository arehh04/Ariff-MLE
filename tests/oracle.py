"""Test helpers around the independent oracle: `fold` lives in scripts/reconcile.py (plain Python, no SQL), so the tests
and the reconcile script share ONE reference implementation of the projection."""

import json
import random

from mle.normalize import Event, Rejected, parse_message
from scripts.reconcile import fold

SNAPSHOT_SQL = """
SELECT registration_number, company_name, nature_of_business, state, industry, source, status, source_updated_at,
       updated_at_missing, embedding_text, embedding_status, embedding_model, md5(embedding::text)
FROM companies ORDER BY registration_number
"""
RESET_SQL = "TRUNCATE company_events, companies, rejected_events RESTART IDENTITY"


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
