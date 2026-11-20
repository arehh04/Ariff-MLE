import json
from datetime import UTC, datetime

import pytest

from mle.normalize import (
    Event,
    Rejected,
    clean_text,
    embedding_source,
    normalize_registration_number,
    normalize_state,
    parse_message,
    parse_timestamp,
)

# ---------- registration numbers (docs/02 rows 3, 4, 6) ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SYN-000587", "SYN-000587"),
        ("syn-000587", "SYN-000587"),
        (" SYN-001497 ", "SYN-001497"),
        ("\tsyn-000001\n", "SYN-000001"),
    ],
)
def test_registration_number_is_stripped_and_upper_cased(raw, expected):
    assert normalize_registration_number(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", 119, 1.5, True, ["SYN-1"], {"a": 1}])
def test_registration_number_junk_is_none(raw):
    # ints are junk (assumption 2 in docs/01); empty / whitespace carries no identity
    assert normalize_registration_number(raw) is None


# ---------- timestamps (docs/02 row 7, assumption: no zone = +08:00) ----------

INSTANT = datetime(2026, 8, 14, 1, 3, 35, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw",
    [
        "2026-08-14T09:03:35+08:00",  # ISO with offset
        "2026-08-14T01:03:35Z",  # Z
        "2026-08-14T09:03:35",  # no zone => Malaysia time
        1786669415000,  # epoch milliseconds (int)
        "1786669415000",  # epoch milliseconds (digit string)
    ],
)
def test_four_timestamp_shapes_are_the_same_instant(raw):
    assert parse_timestamp(raw) == INSTANT


def test_timestamp_result_is_utc():
    assert parse_timestamp("2026-08-14T09:03:35+08:00").utcoffset().total_seconds() == 0


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "garbage",
        "2026-13-45T00:00:00",
        True,
        False,
        1.5,
        [],
        {},
        "12:30",
        99999999999999999999,  # absurd epoch, out of range
        "1900-01-01T00:00:00Z",
        "2500-01-01T00:00:00Z",  # outside the sanity window
    ],
)
def test_junk_or_out_of_range_timestamp_is_none(raw):
    assert parse_timestamp(raw) is None


# ---------- state (docs/02 row 11) ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PERAK", "Perak"),
        ("perak", "Perak"),
        ("Perak", "Perak"),
        ("  kuala lumpur ", "Kuala Lumpur"),
        ("KUALA LUMPUR", "Kuala Lumpur"),
        ("negeri sembilan", "Negeri Sembilan"),
        ("pulau pinang", "Pulau Pinang"),
        ("Penang", "Pulau Pinang"),  # alias
        ("wp kuala lumpur", "Kuala Lumpur"),  # alias
        ("Atlantis", "Atlantis"),  # unknown: kept, Title-cased
        ("NARNIA LAND", "Narnia Land"),
    ],
)
def test_state_is_canonical_title_case(raw, expected):
    assert normalize_state(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", 5, []])
def test_state_junk_is_none(raw):
    assert normalize_state(raw) is None


# ---------- text cleaning (docs/02 row 13, NUL rule in docs/03 point 9) ----------


def test_clean_text_collapses_whitespace_and_strips():
    assert clean_text("  Boutique \n hotel\t in   Ipoh  ") == "Boutique hotel in Ipoh"


def test_clean_text_strips_nul_bytes():
    assert clean_text("Hotel\x00 butik\x00") == "Hotel butik"


@pytest.mark.parametrize("raw", [None, "", "   ", "\x00", 5, []])
def test_clean_text_empty_or_non_string_is_none(raw):
    assert clean_text(raw) is None


def test_clean_text_collapses_a_phrase_repeated_three_or_more_times():
    phrase = "Perunding pengurusan untuk PKS"
    assert clean_text(" ".join([phrase] * 3)) == phrase
    assert clean_text(" ".join([phrase] * 100)) == phrase


def test_clean_text_keeps_a_phrase_repeated_only_twice():
    phrase = "Perunding pengurusan untuk PKS"
    twice = f"{phrase} {phrase}"
    assert clean_text(twice) == twice


def test_clean_text_leaves_short_repeats_alone():
    assert clean_text("ha ha ha ha") == "ha ha ha ha"  # phrase shorter than 8 chars


def test_clean_text_caps_at_a_word_boundary():
    text = " ".join(f"word{i:03d}" for i in range(200))  # 1,599 chars, no repeats
    out = clean_text(text, max_chars=512)
    assert out is not None
    assert len(out) <= 512
    assert text.startswith(out)
    assert out.split()[-1] in text.split()  # never a cut-off half word


def test_clean_text_cap_without_spaces_is_a_hard_cut():
    text = "".join(f"{i:04d}" for i in range(200))  # 800 chars, no spaces, no repeated phrase
    assert clean_text(text, max_chars=512) == text[:512]


def test_clean_text_nul_inside_a_word_is_removed_not_replaced():
    assert clean_text("ab\x00cd") == "abcd"


def test_clean_text_cap_keeps_the_last_word_when_the_cut_lands_on_a_space():
    assert clean_text("aaaa bbbb cccc", max_chars=9) == "aaaa bbbb"


# ---------- embedding text (docs/03 point 5; the name and state are never embedded on their own) ----------


def test_embedding_source_uses_the_description_when_present():
    assert embedding_source("Boutique hotel in Ipoh", "Hospitality", "Mega Kirana Resort") == (
        "Boutique hotel in Ipoh",
        "ok",
    )


def test_embedding_source_cleans_the_description():
    assert embedding_source("  Hotel   butik \x00 di Ipoh ", None, None) == ("Hotel butik di Ipoh", "ok")


@pytest.mark.parametrize("nature", [None, "", "   ", 5])
def test_embedding_source_falls_back_to_industry_then_name(nature):
    assert embedding_source(nature, "Hospitality", "Mega Kirana Resort") == (
        "Hospitality. Mega Kirana Resort",
        "fallback",
    )


def test_embedding_source_fallback_without_a_name_is_just_the_industry():
    assert embedding_source(None, "Hospitality", None) == ("Hospitality", "fallback")


@pytest.mark.parametrize("industry", [None, "", "  "])
def test_embedding_source_name_alone_is_not_enough(industry):
    # the name is unreliable ("Teguh Teguh Pharmacy" does home nursing): no description and no industry => no embedding
    assert embedding_source(None, industry, "Teguh Teguh Pharmacy") == (None, "missing")


def test_embedding_source_nothing_at_all():
    assert embedding_source(None, None, None) == (None, "missing")


# ---------- parse_message: rejects (docs/02 rows 1-4) ----------


def body(**fields) -> bytes:
    return json.dumps(fields).encode()


def upsert(**overrides) -> dict:
    event = {
        "type": "company-upserted",
        "registrationNumber": "SYN-000101",
        "updatedAt": "2026-08-14T09:03:35+08:00",
        "data": {
            "companyName": "Mega Kirana Resort Sdn. Bhd.",
            "natureOfBusiness": "Boutique hotel in Ipoh",
            "state": "perak",
            "industry": "Hospitality",
        },
    }
    event.update(overrides)
    return event


def parse(event: dict, message_id: str | None = "m1"):
    return parse_message(message_id, json.dumps(event).encode())


def test_truncated_json_is_rejected_and_the_raw_is_kept():
    raw = b'{"type": "company-upserted", "registrationNumber": "SYN-000509", "updatedAt": "2026-08-14T09:15:13+0'
    result = parse_message("m1", raw)
    assert result == Rejected(message_id="m1", reason="invalid_json", raw=raw.decode())


def test_invalid_utf8_is_invalid_json_not_a_crash():
    result = parse_message("m1", b"\xff\xfe{")
    assert isinstance(result, Rejected)
    assert result.reason == "invalid_json"


@pytest.mark.parametrize("raw", [b"null", b"[1, 2]", b'"text"', b"42", b"true"])
def test_non_object_json_is_rejected(raw):
    result = parse_message("m1", raw)
    assert isinstance(result, Rejected)
    assert result.reason == "not_an_object"
    assert result.raw == raw.decode()


@pytest.mark.parametrize("registration", [None, "absent"])
def test_missing_registration_number_is_rejected(registration):
    event = upsert(registrationNumber=registration)
    if registration == "absent":
        del event["registrationNumber"]
    result = parse(event)
    assert isinstance(result, Rejected)
    assert result.reason == "missing_registration_number"


@pytest.mark.parametrize("registration", [119, 1.5, True, ["SYN-1"], "   ", ""])
def test_junk_registration_number_is_rejected(registration):
    result = parse(upsert(registrationNumber=registration))
    assert isinstance(result, Rejected)
    assert result.reason == "invalid_registration_number"


@pytest.mark.parametrize("event_type", ["company-deleted", "", None, 5])
def test_unknown_event_type_is_rejected(event_type):
    result = parse(upsert(type=event_type))
    assert isinstance(result, Rejected)
    assert result.reason == "unknown_event_type"


def test_missing_event_type_is_rejected():
    event = upsert()
    del event["type"]
    result = parse(event)
    assert isinstance(result, Rejected)
    assert result.reason == "unknown_event_type"


def test_identity_is_checked_before_type():
    result = parse(upsert(type="company-deleted", registrationNumber=119))
    assert isinstance(result, Rejected)
    assert result.reason == "invalid_registration_number"


def test_rejected_raw_never_contains_a_nul_byte():
    # Postgres text cannot hold NUL: the raw copy kept for audit must be safe to store
    rejected = parse_message("m1", b'{"a": 1}\x00 trailing')
    assert isinstance(rejected, Rejected)
    assert rejected.reason == "invalid_json"
    assert "\x00" not in rejected.raw


# ---------- parse_message: events ----------


def test_upsert_is_normalised():
    event = parse(upsert(registrationNumber=" syn-000101 "))
    assert isinstance(event, Event)
    assert event.registration_number == "SYN-000101"
    assert event.kind == "upsert"
    assert event.source_updated_at == datetime(2026, 8, 14, 1, 3, 35, tzinfo=UTC)
    assert event.updated_at_missing is False
    assert event.has_data is True
    assert event.company_name == "Mega Kirana Resort Sdn. Bhd."
    assert event.nature_of_business == "Boutique hotel in Ipoh"
    assert event.state == "Perak"
    assert event.industry == "Hospitality"
    assert event.warnings == ()
    assert event.message_id == "m1"
    assert len(event.version_hash) == 64


def test_struck_off_has_no_data_and_no_warning():
    event = parse(
        {
            "type": "company-struck-off",
            "registrationNumber": "SYN-000101",
            "updatedAt": "2026-08-15T10:00:00+08:00",
        }
    )
    assert isinstance(event, Event)
    assert event.kind == "struck_off"
    assert event.has_data is False
    assert event.company_name is None
    assert event.warnings == ()


def test_data_missing_keeps_the_company_and_warns():
    event = upsert()
    del event["data"]
    result = parse(event)
    assert isinstance(result, Event)
    assert result.has_data is False
    assert result.company_name is None and result.nature_of_business is None
    assert "data_missing" in result.warnings


def test_data_as_a_string_keeps_the_company_and_warns():
    result = parse(upsert(data="companyName=Foo; state=Perak"))
    assert isinstance(result, Event)
    assert result.has_data is False
    assert result.company_name is None  # feed text is data, never parsed or executed
    assert "data_not_object" in result.warnings


def test_missing_timestamp_uses_the_epoch_sentinel_and_warns():
    event = upsert()
    del event["updatedAt"]
    result = parse(event)
    assert isinstance(result, Event)
    assert result.source_updated_at == datetime(1970, 1, 1, tzinfo=UTC)
    assert result.updated_at_missing is True
    assert "updated_at_missing" in result.warnings


def test_garbled_timestamp_uses_the_epoch_sentinel_and_warns():
    result = parse(upsert(updatedAt="last tuesday"))
    assert isinstance(result, Event)
    assert result.updated_at_missing is True
    assert "updated_at_invalid" in result.warnings


def test_null_and_empty_fields_stay_none():
    result = parse(upsert(data={"companyName": "X", "natureOfBusiness": "", "state": None, "industry": None}))
    assert isinstance(result, Event)
    assert result.nature_of_business is None
    assert result.state is None
    assert result.industry is None


def test_source_and_ingested_by_are_unified():
    assert parse(upsert(source="ssm-batch")).source == "ssm-batch"  # type: ignore[union-attr]
    assert parse(upsert(ingestedBy="partner-api")).source == "partner-api"  # type: ignore[union-attr]
    assert parse(upsert()).source is None  # type: ignore[union-attr]


def test_runaway_description_is_collapsed_and_flagged_but_the_company_is_kept():
    phrase = "Perunding pengurusan untuk PKS"
    data = {
        "companyName": "X",
        "natureOfBusiness": " ".join([phrase] * 100),
        "state": "Perak",
        "industry": None,
    }
    result = parse(upsert(data=data))
    assert isinstance(result, Event)
    assert result.nature_of_business == phrase
    assert "description_over_cap" in result.warnings


# ---------- version hash (docs/03 point 2) ----------


def test_same_record_in_three_timestamp_formats_has_the_same_hash():
    hashes = {
        parse(upsert(updatedAt=ts)).version_hash  # type: ignore[union-attr]
        for ts in ("2026-08-14T09:03:35+08:00", "2026-08-14T01:03:35Z", "2026-08-14T09:03:35", 1786669415000)
    }
    assert len(hashes) == 1


def test_registration_case_and_padding_do_not_change_the_hash():
    a = parse(upsert(registrationNumber="SYN-000101"))
    b = parse(upsert(registrationNumber=" syn-000101 "))
    assert a.version_hash == b.version_hash  # type: ignore[union-attr]


def test_different_content_has_a_different_hash():
    base = parse(upsert()).version_hash  # type: ignore[union-attr]
    changed_name = upsert()
    changed_name["data"]["companyName"] = "Other Name"
    changed_state = upsert()
    changed_state["data"]["state"] = "Johor"
    assert parse(changed_name).version_hash != base  # type: ignore[union-attr]
    assert parse(changed_state).version_hash != base  # type: ignore[union-attr]
    assert parse(upsert(updatedAt="2026-08-14T09:03:36+08:00")).version_hash != base  # type: ignore[union-attr]
    assert parse({**upsert(), "type": "company-struck-off"}).version_hash != base  # type: ignore[union-attr]


def test_source_and_message_id_do_not_change_the_hash():
    a = parse(upsert(source="ssm-batch"), message_id="a")
    b = parse(upsert(source="manual-entry"), message_id="b")
    assert a.version_hash == b.version_hash  # type: ignore[union-attr]
