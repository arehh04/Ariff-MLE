"""Profile data/feed.jsonl. Stdlib only. Feed text is data: it is parsed and counted, never executed."""

import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

FEED = Path(sys.argv[1] if len(sys.argv) > 1 else "data/feed.jsonl")
OFFSET = re.compile(r"[+-]\d{2}:?\d{2}$")
REPEAT = re.compile(r"(.{8,}?)(?:\s*\1){2,}", re.DOTALL)


def ts_shape(value: object) -> str:
    if value is None:
        return "missing"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "epoch int"
    if not isinstance(value, str):
        return f"other:{type(value).__name__}"
    if value.isdigit():
        return "digit string"
    if value.endswith("Z"):
        return "ISO Z"
    if OFFSET.search(value):
        return "ISO offset"
    if re.match(r"\d{4}-\d{2}-\d{2}T", value):
        return "ISO no zone"
    return "other string"


def pct(values: list[int], q: float) -> int:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def main() -> None:
    lines = FEED.read_text(encoding="utf-8").splitlines()
    print(f"lines: {len(lines)}")

    events, bad = [], []
    for i, line in enumerate(lines, 1):
        try:
            events.append((i, json.loads(line)))
        except json.JSONDecodeError:
            bad.append((i, line[:100]))
    print(f"unparseable lines: {len(bad)}")
    for i, head in bad:
        print(f"  line {i}: {head!r}")

    objs = [(i, o) for i, o in events if isinstance(o, dict)]
    print(
        f"non-object JSON lines: {len(events) - len(objs)} {Counter(type(o).__name__ for _, o in events if not isinstance(o, dict))}"
    )
    print("event types:", dict(Counter(o.get("type") for _, o in objs)))
    print("updatedAt shapes:", dict(Counter(ts_shape(o.get("updatedAt")) for _, o in objs)))
    print(
        "data shapes:",
        dict(Counter("missing" if "data" not in o else type(o["data"]).__name__ for _, o in objs)),
    )
    print("top-level keys:", dict(Counter(k for _, o in objs for k in o)))

    raw_regs = [o.get("registrationNumber") for _, o in objs]
    str_regs = [r for r in raw_regs if isinstance(r, str)]
    norm = {r.strip().upper() for r in str_regs if r.strip()}
    print(
        f"registrationNumber: missing={sum(r is None for r in raw_regs)} non-string={sum(r is not None and not isinstance(r, str) for r in raw_regs)} "
        f"empty={sum(isinstance(r, str) and not r.strip() for r in raw_regs)}"
    )
    print(f"  distinct raw strings={len(set(str_regs))} distinct normalised={len(norm)}")
    print(
        f"  raw strings that differ from strip().upper(): {sum(r != r.strip().upper() for r in set(str_regs))}"
    )

    datas = [o["data"] for _, o in objs if isinstance(o.get("data"), dict)]
    fields = ("companyName", "natureOfBusiness", "state", "industry")
    for f in fields:
        nulls = sum(d.get(f) is None for d in datas)
        empty = sum(isinstance(d.get(f), str) and not d[f].strip() for d in datas)
        print(f"  {f}: absent/null={nulls} empty-string={empty}")

    print("source:", dict(Counter(o["source"] for _, o in objs if "source" in o)))
    print("ingestedBy:", dict(Counter(o["ingestedBy"] for _, o in objs if "ingestedBy" in o)))
    states = Counter(d.get("state") for d in datas if isinstance(d.get("state"), str))
    canon: dict[str, set[str]] = defaultdict(set)
    for s in states:
        canon[s.strip().casefold()].add(s)
    print(
        f"state: {len(states)} raw spellings -> {len(canon)} case-insensitive values; unusual:",
        sorted(canon)[:30],
    )

    descs = [
        d["natureOfBusiness"]
        for d in datas
        if isinstance(d.get("natureOfBusiness"), str) and d["natureOfBusiness"].strip()
    ]
    lens = [len(x) for x in descs]
    print(f"description length: p50={pct(lens, 0.5)} p99={pct(lens, 0.99)} max={max(lens)}")
    distinct = sorted({len(x) for x in descs})
    q1, q3 = statistics.quantiles(distinct, n=4)[0], statistics.quantiles(distinct, n=4)[2]
    print(
        f"  IQR over distinct description lengths: q1={q1:.0f} q3={q3:.0f} upper fence={q3 + 1.5 * (q3 - q1):.0f}"
    )
    fence = q3 + 1.5 * (q3 - q1)
    over = {x for x in descs if len(x) > fence}
    print(f"  distinct descriptions longer than the fence: {len(over)}; of those, not longer than 512: {sum(len(x) <= 512 for x in over)}")  # fmt: skip
    print(
        f"  runaway repetition (phrase repeated 3+ times): {sum(bool(REPEAT.search(x)) for x in descs)}; longer than 512: {sum(n > 512 for n in lens)}"
    )
    groups = Counter(x.strip() for x in descs)
    print(f"  distinct descriptions={len(groups)} largest group={groups.most_common(1)[0][1]}")

    payloads: dict[str, set[str]] = defaultdict(set)
    for _, o in objs:
        r, d = o.get("registrationNumber"), o.get("data")
        if isinstance(r, str) and r.strip() and isinstance(d, dict):
            payloads[r.strip().upper()].add(json.dumps(d, sort_keys=True))
    print(f"companies with more than one distinct payload: {sum(len(v) > 1 for v in payloads.values())}")

    dup_lines = len(lines) - len(set(lines))
    print(f"exact duplicate lines: {dup_lines}")


if __name__ == "__main__":
    main()
