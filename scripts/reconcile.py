"""Independent check: fold the feed in plain Python (no SQL) and diff the result against the database.

    uv run python -m scripts.reconcile [--file data/feed.jsonl] [--model NAME]     # exit code 1 on any mismatch

Checks: companies missing / unexpected / with wrong values (every stored field), struck-off count, companies with no
embedding, the event log, and the rejects table (expected vs stored). Run it after `just publish` and after the consumer drains.
Rules folded (AGENTS.md "Decisions made"): newest non-null value per field wins, ordered by (source_updated_at, version_hash);
status comes from the newest event that strikes off or carries data; duplicates collapse (same version_hash).
"""

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from mle import db
from mle.config import get_settings
from mle.normalize import Event, Rejected, embedding_source, parse_message

FEED = Path(__file__).resolve().parents[1] / "data" / "feed.jsonl"
FIELDS = ("company_name", "nature_of_business", "state", "industry", "source")
COLUMNS = ("registration_number", *FIELDS, "status", "source_updated_at", "updated_at_missing", "embedding_text",
           "embedding_status", "embedding_model")  # fmt: skip
SHOW = 8  # examples printed per problem
STORED_SQL = """
SELECT registration_number, company_name, nature_of_business, state, industry, source, status, source_updated_at,
       updated_at_missing, embedding_text, embedding_status, embedding_model
FROM companies
"""  # same order as COLUMNS


def fold(events: list[Event], model: str) -> dict[str, tuple]:
    """Expected `companies` rows, in COLUMNS order."""
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


def parse_feed(path: Path) -> tuple[list[Event], list[Rejected], int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    parsed = [parse_message(None, line.encode()) for line in lines]
    return (
        [m for m in parsed if isinstance(m, Event)],
        [m for m in parsed if isinstance(m, Rejected)],
        len(lines),
    )


def _sample(items) -> str:
    items = sorted(items, key=str)
    return ", ".join(map(str, items[:SHOW])) + (
        f" ... (+{len(items) - SHOW} more)" if len(items) > SHOW else ""
    )


def reconcile(conn, feed: Path, model: str) -> tuple[list[str], list[str]]:
    """Returns (problems, report lines). No problems means the database matches the feed."""
    events, rejects, lines = parse_feed(feed)
    expected = fold(events, model)
    cur = conn.execute(STORED_SQL)
    stored = {row[0]: row for row in cur.fetchall()}
    problems: list[str] = []
    report = [f"feed: {lines} lines = {len(events)} events + {len(rejects)} unattributable"]

    missing, unexpected = expected.keys() - stored.keys(), stored.keys() - expected.keys()
    report.append(f"companies: expected {len(expected)}, stored {len(stored)}")
    if missing:
        problems.append(f"{len(missing)} companies MISSING from the database: {_sample(missing)}")
    if unexpected:
        problems.append(f"{len(unexpected)} UNEXPECTED companies in the database: {_sample(unexpected)}")

    wrong = [(reg, col, expected[reg][i], stored[reg][i])
             for reg in expected.keys() & stored.keys() for i, col in enumerate(COLUMNS) if expected[reg][i] != stored[reg][i]]  # fmt: skip
    if wrong:
        problems.append(
            f"{len(wrong)} wrong values (registration, column, expected, stored): {_sample(wrong)}"
        )

    count = lambda rows, status: sum(1 for r in rows if r[COLUMNS.index("status")] == status)
    struck = (count(expected.values(), "struck_off"), count(stored.values(), "struck_off"))
    report.append(f"struck off: expected {struck[0]}, stored {struck[1]}")
    if struck[0] != struck[1]:
        problems.append(f"struck-off count differs: expected {struck[0]}, stored {struck[1]}")

    no_text = {reg for reg, row in expected.items() if row[COLUMNS.index("embedding_text")] is None}
    no_vector = {
        r[0] for r in conn.execute("SELECT registration_number FROM companies WHERE embedding IS NULL")
    }
    report.append(f"without embedding: expected {len(no_text)}, stored {len(no_vector)}")
    if no_text != no_vector:
        problems.append(f"embedding presence differs: expected-but-missing {_sample(no_text ^ no_vector)}")

    want_events = {(e.registration_number, e.version_hash) for e in events}
    have_events = {
        tuple(r) for r in conn.execute("SELECT registration_number, version_hash FROM company_events")
    }
    report.append(f"events: expected {len(want_events)}, stored {len(have_events)}")
    if want_events != have_events:
        problems.append(
            f"event log differs: {len(want_events - have_events)} missing, {len(have_events - want_events)} unexpected"
        )

    want_rejects = Counter((r.reason, r.raw) for r in rejects)
    have_rejects = {
        (r[0], r[1]): r[2] for r in conn.execute("SELECT reason, raw, seen_count FROM rejected_events")
    }
    report.append(f"rejects: {sum(want_rejects.values())} lines in {len(want_rejects)} distinct rows; "
                  f"stored {len(have_rejects)} rows, seen_count sum {sum(have_rejects.values())}")  # fmt: skip
    if want_rejects.keys() != have_rejects.keys():
        problems.append(f"reject rows differ: {len(want_rejects.keys() - have_rejects.keys())} missing, "
                        f"{len(have_rejects.keys() - want_rejects.keys())} unexpected")  # fmt: skip
    short = [
        k for k, n in want_rejects.items() if have_rejects.get(k, 0) < n
    ]  # redeliveries may raise it, never lower it
    if short:
        problems.append(
            f"{len(short)} reject rows counted fewer times than they appear in the feed: {_sample(short)}"
        )
    return problems, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", type=Path, default=FEED)
    parser.add_argument(
        "--model", default=get_settings().embedding_model, help="embedding model name stored on the rows"
    )
    args = parser.parse_args(argv)
    conn = db.connect()
    problems, report = reconcile(conn, args.file, args.model)
    print("\n".join(report))
    if problems:
        print("\nMISMATCH:\n  " + "\n  ".join(problems))
        return 1
    print("\nOK: the database matches an independent fold of the feed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
