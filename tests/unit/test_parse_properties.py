"""Properties of parse_message that must hold for ANY input: fuzzed (seeded) and table-driven."""

import json
import random
from datetime import UTC, datetime

import pytest

from mle.normalize import DESCRIPTION_CAP, EPOCH, MALAYSIA, Event, Rejected, parse_message

BASE = {
    "type": "company-upserted", "registrationNumber": "SYN-000101", "updatedAt": "2026-08-14T09:00:00+08:00",
    "data": {"companyName": "Acme Sdn. Bhd.", "natureOfBusiness": "Boutique hotel in Ipoh", "state": "Perak", "industry": "Hospitality"},
}  # fmt: skip
WEIRD = [None, 0, -1, 3.14, True, [], {}, [1, 2], {"a": {"b": []}}, "", "  ", "\x00", "a\x00b", "x" * 5000, "é" * 700,
         "🙂 emoji", "line\nbreak\ttab", "<script>alert(1)</script>", "'; DROP TABLE companies; --", "{{7*7}}", "%s %(x)s"]  # fmt: skip


def hash_of(**overrides) -> str:
    payload = json.loads(json.dumps(BASE))
    for key, value in overrides.items():
        if key in payload["data"]:
            payload["data"][key] = value
        else:
            payload[key] = value
    event = parse_message(None, json.dumps(payload).encode())
    assert isinstance(event, Event)
    return event.version_hash


def mutate(rng: random.Random) -> bytes:
    payload = json.loads(json.dumps(BASE))
    strategy = rng.randrange(7)
    if strategy == 0:
        return bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
    if strategy == 1:
        raw = json.dumps(payload).encode()
        return raw[: rng.randrange(len(raw))]
    if strategy == 2:
        payload[rng.choice(list(payload))] = rng.choice(WEIRD)
    elif strategy == 3:
        payload["data"][rng.choice(list(payload["data"]))] = rng.choice(WEIRD)
    elif strategy == 4:
        del payload[rng.choice(list(payload))]
    elif strategy == 5:
        return b"[" * rng.randrange(1, 2000) + b"]" * rng.randrange(0, 2000)
    else:
        return json.dumps(payload).encode()[:-1] + b"\xff\xfe"
    return json.dumps(payload).encode()


def check_invariants(result: Event | Rejected) -> None:
    if isinstance(result, Rejected):
        assert result.reason in {"invalid_json", "not_an_object", "missing_registration_number",
                                 "invalid_registration_number", "unknown_event_type"}  # fmt: skip
        assert "\x00" not in result.raw
        return
    assert (
        result.registration_number == result.registration_number.strip().upper()
        and result.registration_number
    )
    assert len(result.version_hash) == 64 and set(result.version_hash) <= set("0123456789abcdef")
    assert (
        result.source_updated_at.tzinfo is not None
        and result.source_updated_at.utcoffset().total_seconds() == 0
    )
    assert (result.source_updated_at == EPOCH) == result.updated_at_missing
    assert result.kind in {"upsert", "struck_off"}
    if result.kind == "struck_off":
        assert not result.has_data
    if not result.has_data:
        assert (result.company_name, result.nature_of_business, result.state, result.industry) == (None,) * 4
    for text in (
        result.company_name,
        result.nature_of_business,
        result.state,
        result.industry,
        result.source,
    ):
        assert text is None or ("\x00" not in text and text.strip() == text and text != "")
    assert result.nature_of_business is None or len(result.nature_of_business) <= DESCRIPTION_CAP
    assert all(isinstance(w, str) and w for w in result.warnings)


@pytest.mark.parametrize("seed", range(50))
def test_fuzzed_input_never_raises_and_always_satisfies_the_invariants(seed):
    rng = random.Random(seed)
    for _ in range(40):
        check_invariants(parse_message("m", mutate(rng)))


@pytest.mark.parametrize("value", WEIRD, ids=lambda v: repr(v)[:20])
@pytest.mark.parametrize("field", ["companyName", "natureOfBusiness", "state", "industry"])
def test_any_value_in_any_data_field_is_survived(field, value):
    payload = json.loads(json.dumps(BASE))
    payload["data"][field] = value
    check_invariants(parse_message(None, json.dumps(payload).encode()))


# ---- the hash: same content => same hash, any content change => different hash ---------------------------------

INSTANTS = [datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), datetime(2026, 8, 14, 1, 0, 0, tzinfo=UTC),
            datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC), datetime(2026, 2, 28, 12, 30, 15, tzinfo=UTC),
            datetime(2030, 6, 1, 8, 0, 0, tzinfo=UTC), datetime(2001, 1, 1, 16, 0, 0, tzinfo=UTC)]  # fmt: skip


@pytest.mark.parametrize("instant", INSTANTS, ids=lambda d: d.isoformat())
def test_same_instant_in_every_timestamp_format_hashes_identically(instant):
    local = instant.astimezone(MALAYSIA)
    formats = [instant.isoformat().replace("+00:00", "Z"), local.isoformat(), local.replace(tzinfo=None).isoformat(),
               int(instant.timestamp() * 1000)]  # fmt: skip
    assert len({hash_of(updatedAt=f) for f in formats}) == 1


@pytest.mark.parametrize(
    ("field", "other"),
    [("companyName", "Other Sdn Bhd"), ("natureOfBusiness", "Palm oil"), ("state", "Johor"),
     ("industry", "Retail"), ("updatedAt", "2026-08-14T09:00:01+08:00"), ("type", "company-struck-off")],
)  # fmt: skip
def test_changing_any_content_field_changes_the_hash(field, other):
    assert hash_of(**{field: other}) != hash_of()


@pytest.mark.parametrize("overrides", [{"source": "ssm-batch"}, {"ingestedBy": "partner-api"}, {"extra": 1},
                                       {"companyName": "  Acme   Sdn. Bhd.  "}, {"state": "PERAK"}, {"state": " perak "}])  # fmt: skip
def test_audit_only_and_cosmetic_differences_do_not_change_the_hash(overrides):
    assert hash_of(**overrides) == hash_of()


def test_message_id_is_not_part_of_the_hash():
    a = parse_message("one", json.dumps(BASE).encode())
    b = parse_message("two", json.dumps(BASE).encode())
    assert a.version_hash == b.version_hash and a.message_id != b.message_id


@pytest.mark.parametrize("variant", [" syn-000101", "SYN-000101 ", "Syn-000101", "\tSYN-000101\n"])
def test_registration_number_variants_share_identity_and_hash(variant):
    assert hash_of(registrationNumber=variant) == hash_of()
    assert (
        parse_message(None, json.dumps({**BASE, "registrationNumber": variant}).encode()).registration_number
        == "SYN-000101"
    )


@pytest.mark.parametrize(
    "text", ["ab " * 400, "Boutique hotel. " * 300, "x" * 3359, "Hotel butik di Ipoh " * 160]
)
def test_runaway_descriptions_are_capped_collapsed_and_flagged_or_short(text):
    event = parse_message(
        None, json.dumps({**BASE, "data": {**BASE["data"], "natureOfBusiness": text}}).encode()
    )
    assert isinstance(event, Event) and 0 < len(event.nature_of_business) <= DESCRIPTION_CAP
