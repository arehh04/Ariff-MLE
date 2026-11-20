"""Pure parsing and normalisation of registry feed messages. No I/O, no database, no clock.

Decisions (see AGENTS.md "Decisions made"): timestamps without an offset are Malaysia time (+08:00);
odd values are flagged with warnings and kept, never dropped.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

MALAYSIA = timezone(timedelta(hours=8))
# Sanity window for timestamps: anything outside is treated as garbage (None).
_MIN_YEAR, _MAX_YEAR = 2000, 2100


def _scrub(value: str) -> str:
    """Replace lone surrogates (valid JSON like "\\ud800", but not encodable as UTF-8) with U+FFFD."""
    return value if value.isascii() else value.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


def normalize_registration_number(value: object) -> str | None:
    """Identity of a company: a string, stripped and upper-cased. Non-strings (e.g. ints) are junk."""
    if not isinstance(value, str):
        return None
    normalised = _scrub(value).strip().upper()
    return normalised or None


def _in_window(moment: datetime) -> bool:
    return _MIN_YEAR <= moment.year <= _MAX_YEAR


def parse_timestamp(value: object) -> datetime | None:
    """Return the instant as an aware UTC datetime, or None if it cannot be trusted.

    Accepted: ISO-8601 with offset or Z, ISO without zone (read as +08:00),
    epoch milliseconds as an int or a string of digits.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            moment = datetime.fromtimestamp(int(value) / 1000, tz=UTC)
        elif isinstance(value, str):
            moment = datetime.fromisoformat(value.strip())
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=MALAYSIA)
            moment = moment.astimezone(UTC)
        else:
            return None
    except ValueError, OverflowError, OSError:
        return None
    return moment if _in_window(moment) else None


_STATES = (
    "Johor", "Kedah", "Kelantan", "Kuala Lumpur", "Labuan", "Melaka", "Negeri Sembilan", "Pahang",
    "Perak", "Perlis", "Pulau Pinang", "Putrajaya", "Sabah", "Sarawak", "Selangor", "Terengganu",
)  # fmt: skip
_STATE_BY_KEY = {name.casefold(): name for name in _STATES}
_STATE_ALIASES = {
    "penang": "Pulau Pinang",
    "malacca": "Melaka",
    "kl": "Kuala Lumpur",
    "wp kuala lumpur": "Kuala Lumpur",
    "wilayah persekutuan kuala lumpur": "Kuala Lumpur",
    "wp putrajaya": "Putrajaya",
    "wp labuan": "Labuan",
}


def normalize_state(value: object) -> str | None:
    """Canonical Title Case for the 16 Malaysian states (plus a few aliases). Unknown text is kept Title-cased.

    The state in the feed is unreliable (it can contradict the description), so it is stored but never embedded.
    """
    text = clean_text(value)
    if text is None:
        return None
    key = text.casefold()
    return _STATE_BY_KEY.get(key) or _STATE_ALIASES.get(key) or text.title()


# A phrase of 8-256 characters followed by itself at least twice more (so 3+ copies in total). The upper bound keeps
# the search linear in the text length; an unbounded phrase made it quadratic (20,000 characters took ~8 s).
_REPEATED_PHRASE = re.compile(r"(.{8,256}?)(?:\s*\1){2,}", re.DOTALL)
_MAX_INPUT = 20_000  # shortcut: look at no more than this per field (real texts are < 3,400); upgrade if a longer runaway matters
_WHITESPACE = re.compile(r"\s+")


def clean_text(value: object, max_chars: int = 512) -> str | None:
    """Make free text safe to store and embed; None when nothing useful is left.

    Steps, in order: drop NUL bytes (Postgres text cannot hold them), collapse whitespace, collapse a phrase that is
    repeated 3+ times down to one copy (the feed has runaway repeats up to 3,359 chars), then cap at a word boundary.
    """
    if not isinstance(value, str):
        return None
    text = _WHITESPACE.sub(" ", _scrub(value[:_MAX_INPUT]).replace("\x00", "")).strip()
    text = _REPEATED_PHRASE.sub(r"\1", text)
    if len(text) > max_chars:
        cut = text[:max_chars]
        if text[max_chars] != " ":  # we are mid-word: back up to the previous space
            head, space, _ = cut.rpartition(" ")
            cut = head if space and head else cut
        text = cut
    text = text.strip()
    return text or None


def embedding_source(nature: object, industry: object, name: object) -> tuple[str | None, str]:
    """Pick the text to embed and say where it came from: "ok", "fallback" or "missing".

    The description is the signal. Without one we fall back to "industry. name". The name alone is never
    embedded because names are unreliable, so a company with neither description nor industry gets no embedding.
    """
    description = clean_text(nature)
    if description:
        return description, "ok"
    industry_text = clean_text(industry)
    if industry_text:
        name_text = clean_text(name)
        return (f"{industry_text}. {name_text}" if name_text else industry_text), "fallback"
    return None, "missing"


DESCRIPTION_CAP = 512
EPOCH = datetime(
    1970, 1, 1, tzinfo=UTC
)  # sentinel for "the feed gave no usable time": loses to any real timestamp
_KINDS = {"company-upserted": "upsert", "company-struck-off": "struck_off"}


@dataclass(frozen=True)
class Event:
    """One identified, normalised registry event. Facts only: projection rules live elsewhere."""

    registration_number: str
    kind: str  # "upsert" | "struck_off"
    source_updated_at: datetime  # UTC; EPOCH when missing
    updated_at_missing: bool
    has_data: bool  # False for struck-off, or an upsert without a usable `data` object
    company_name: str | None
    nature_of_business: str | None
    state: str | None
    industry: str | None
    source: str | None  # `source` and `ingestedBy` unified; audit only, not part of the hash
    warnings: tuple[str, ...]
    version_hash: str
    message_id: str | None


@dataclass(frozen=True)
class Rejected:
    """A message with no usable identity. Kept raw so nothing is silently lost."""

    message_id: str | None
    reason: str
    raw: str


def _version_hash(
    kind: str, moment: datetime, missing: bool, has_data: bool, fields: tuple[str | None, ...]
) -> str:
    """sha256 of the normalised content: the same record in another timestamp format hashes the same."""
    content = [kind, moment.isoformat(), missing, has_data, *fields]
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def parse_message(message_id: str | None, body: bytes) -> Event | Rejected:
    """Turn one raw message into an Event, or a Rejected(reason, raw). Never raises on bad input.

    Reject reasons: invalid_json, not_an_object, missing_registration_number, invalid_registration_number,
    unknown_event_type. Identity is checked before the event type. Everything else (no timestamp, no `data`,
    odd values) keeps the event and adds a warning.
    """
    raw = body.decode("utf-8", errors="replace").replace("\x00", "")

    def reject(reason: str) -> Rejected:
        return Rejected(message_id=message_id, reason=reason, raw=raw)

    try:
        payload = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError, ValueError, RecursionError:
        return reject("invalid_json")
    if not isinstance(payload, dict):
        return reject("not_an_object")

    if payload.get("registrationNumber") is None:
        return reject("missing_registration_number")
    registration = normalize_registration_number(payload["registrationNumber"])
    if registration is None:
        return reject("invalid_registration_number")

    event_type = payload.get("type")
    kind = _KINDS.get(event_type) if isinstance(event_type, str) else None
    if kind is None:
        return reject("unknown_event_type")

    warnings: list[str] = []
    moment = parse_timestamp(payload.get("updatedAt"))
    updated_at_missing = moment is None
    if moment is None:
        moment = EPOCH
        warnings.append("updated_at_missing" if payload.get("updatedAt") is None else "updated_at_invalid")

    data = payload.get("data")
    has_data = kind == "upsert" and isinstance(data, dict)
    if kind == "upsert" and not has_data:
        warnings.append("data_missing" if data is None else "data_not_object")

    name = nature = state = industry = None
    if has_data:
        name = clean_text(data.get("companyName"))
        nature = clean_text(data.get("natureOfBusiness"), DESCRIPTION_CAP)
        state = normalize_state(data.get("state"))
        industry = clean_text(data.get("industry"))
        original = data.get("natureOfBusiness")
        if isinstance(original, str) and len(original) > DESCRIPTION_CAP:
            warnings.append("description_over_cap")

    source = next(
        (_scrub(v) for v in (payload.get("source"), payload.get("ingestedBy")) if isinstance(v, str) and v),
        None,
    )
    return Event(
        registration_number=registration,
        kind=kind,
        source_updated_at=moment,
        updated_at_missing=updated_at_missing,
        has_data=has_data,
        company_name=name,
        nature_of_business=nature,
        state=state,
        industry=industry,
        source=source,
        warnings=tuple(warnings),
        version_hash=_version_hash(
            kind, moment, updated_at_missing, has_data, (name, nature, state, industry)
        ),
        message_id=message_id,
    )
