"""Deep dive: recommendation quality. Does "similar company" mean "same business type", in English AND Malay?

Labels come from keyword rules below (English + Malay pattern per business type), written by reading the 172 distinct
descriptions. They use neither a model nor the feed's coarse `industry` field. Which pattern matched also tells us the
language of the description, so the Malay/English split needs no separate heuristic.

Metric, per query description q (identical text is never a candidate; each distinct text appears once):
  P@10   = share of the 10 nearest other descriptions that have q's business type
  capped = hits / min(10, R), R = how many same-type candidates exist. P@10 cannot reach 1.0 for a type with fewer than
           10 same-type texts (e.g. "Durian orchard" has only its Malay twin), so `capped` is the fairer score.
Each query is weighted by the number of companies carrying that description, so common texts matter more.

Run: uv run python -m scripts.eval_quality [--models hashing bge-small-en-v1.5 paraphrase-multilingual-MiniLM-L12-v2]
Not measured: whether a human finds the neighbours useful, ranking beyond the top 10, struck-off or stale companies,
and companies whose description is missing (they have no vector at all). Business type is a proxy for "similar".
"""

import argparse
import re
from collections import Counter

import numpy as np

from mle.embedding import HashingEmbedder
from mle.normalize import Event, parse_message
from scripts.eval_embeddings import FEED, MODELS, fastembed_embedder

K = 10

# (business type, English pattern, Malay pattern). First rule that matches either pattern wins, so order matters:
# specific phrases come before the generic words they contain ("plastic packaging" before parcel "bungkusan").
RULES: list[tuple[str, str, str]] = [
    ("software", r"software development", r"pembangunan perisian"),
    ("mobile-app", r"mobile app development", r"(?!x)x"),
    ("it-services", r"it support|cloud hosting", r"sokongan it"),
    ("accounting", r"accounting, tax", r"perakaunan, cukai"),
    ("training", r"training courses", r"(?!x)x"),
    ("tuition", r"tuition", r"tuisyen"),
    ("kindergarten", r"kindergarten", r"tadika"),
    ("consulting", r"management consulting", r"perunding pengurusan"),
    ("recruitment", r"recruitment", r"(?!x)x"),
    ("architecture", r"architectural", r"(?!x)x"),
    ("civil-engineering", r"civil engineering", r"kejuruteraan awam"),
    ("general-contractor", r"general contractor", r"kontraktor am"),
    ("steel-structures", r"steel structures", r"(?!x)x"),
    ("renovation", r"renovation", r"pengubahsuaian"),
    ("hotel", r"boutique hotel", r"hotel butik"),
    ("homestay", r"homestay and chalet", r"pengusaha homestay"),
    ("travel-agency", r"travel agency", r"agensi pelancongan"),
    ("cafe", r"cafes", r"kedai kopi"),
    ("restaurant", r"restaurant serving", r"restoran"),
    ("catering", r"catering", r"katering"),
    ("bakery", r"bakery", r"roti dan kek"),
    ("grocery", r"mini market", r"kedai runcit barangan"),
    ("frozen-wholesale", r"wholesale distribution of frozen", r"(?!x)x"),
    ("cold-chain", r"cold chain", r"sejuk beku"),
    ("trucking", r"trucking", r"(?!x)x"),
    ("freight", r"freight forwarding", r"fret"),
    ("plastic-packaging", r"plastic packaging", r"bungkusan plastik"),
    ("parcel-delivery", r"parcel delivery", r"penghantaran bungkusan"),
    ("clinic", r"general practice clinic", r"klinik perubatan"),
    ("dental", r"dental", r"(?!x)x"),
    ("pharmacy", r"pharmacy", r"farmasi"),
    ("elder-care", r"home nursing", r"warga emas"),
    ("online-retail", r"online retail", r"dalam talian"),
    ("phone-retail", r"mobile phones", r"telefon bimbit"),
    ("textile-retail", r"textiles", r"(?!x)x"),
    ("durian", r"durian orchard", r"kebun durian"),
    ("oil-palm", r"oil palm", r"kelapa sawit"),
    ("hydroponics", r"hydroponic", r"(?!x)x"),
    ("poultry", r"poultry", r"penternakan ayam"),
    ("furniture", r"furniture", r"perabot"),
    ("rubber-gloves", r"rubber gloves", r"sarung tangan"),
    ("machining", r"precision machining", r"(?!x)x"),
]
COMPILED = [(t, re.compile(en, re.IGNORECASE), re.compile(ms, re.IGNORECASE)) for t, en, ms in RULES]
CITY = re.compile(
    r"\b(Ipoh|Johor Bahru|Kota Kinabalu|Kuantan|Kuching|Melaka|Penang|Seremban|Port Klang|"
    r"(?:the )?Klang Valley|Kuala Lumpur)\b",
    re.IGNORECASE,
)


def label(text: str) -> tuple[str, str] | None:
    """(business type, 'en' or 'ms') for a description, or None when no rule matches."""
    for business_type, en, ms in COMPILED:
        if en.search(text):
            return business_type, "en"
        if ms.search(text):
            return business_type, "ms"
    return None


def load() -> dict[str, int]:
    """Distinct description -> number of distinct companies (registration numbers) that carry it, read from the feed."""
    companies: dict[str, set[str]] = {}
    for line in FEED.read_text(encoding="utf-8").splitlines():
        event = parse_message(None, line.encode())
        if isinstance(event, Event) and event.nature_of_business:
            companies.setdefault(event.nature_of_business, set()).add(event.registration_number)
    return {text: len(regs) for text, regs in companies.items()}


def score(vectors: np.ndarray, types: list[str], weights: list[int], langs: list[str]) -> dict:
    sims = vectors @ vectors.T
    rows = []
    for i, query_type in enumerate(types):
        others = [j for j in range(len(types)) if j != i]
        relevant = sum(types[j] == query_type for j in others)
        top = sorted(others, key=lambda j: -sims[i, j])[:K]
        hits = sum(types[j] == query_type for j in top)
        rows.append((i, hits / K, hits / min(K, relevant) if relevant else np.nan, weights[i], langs[i]))
    return {"rows": rows}


def weighted(rows: list, column: int, lang: str | None) -> float:
    picked = [r for r in rows if (lang is None or r[4] == lang) and not np.isnan(r[column])]
    return float(np.average([r[column] for r in picked], weights=[r[3] for r in picked]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", nargs="*", default=["hashing", *list(MODELS)[:2]])
    parser.add_argument("--worst", type=int, default=6, help="weakest queries to list per model")
    args = parser.parse_args()

    counts = load()
    texts = sorted(counts)
    labelled = {t: label(t) for t in texts}
    unlabelled = [t for t, lab in labelled.items() if lab is None]
    assert not unlabelled, f"no rule matches: {unlabelled}"
    types = [labelled[t][0] for t in texts]
    langs = [labelled[t][1] for t in texts]
    weights = [counts[t] for t in texts]
    masked = [CITY.sub("CITY", t) for t in texts]
    per_type = Counter(types)
    print(f"{len(texts)} distinct descriptions, {sum(weights)} company-description pairs, {len(per_type)} business types, "
          f"{langs.count('en')} English / {langs.count('ms')} Malay")  # fmt: skip
    print(f"same-type texts per query: min {min(per_type.values()) - 1}, max {max(per_type.values()) - 1} "
          f"(P@{K} ceiling is below 1.0 when fewer than {K})\n")  # fmt: skip

    header = f"{'model':40s} {'text':6s} {'P@10':>6s} {'capped':>7s} {'en capped':>10s} {'ms capped':>10s}"
    print(header + "\n" + "-" * len(header))
    details = {}
    for name in args.models:
        if name == "hashing":
            embed = HashingEmbedder().embed
        else:
            model_id, prefix = MODELS[name]
            embed, _ = fastembed_embedder(model_id, prefix, cuda=False)
        for variant, inputs in (("raw", texts), ("masked", masked)):
            vectors = np.asarray(embed(inputs), dtype=np.float64)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
            rows = score(vectors, types, weights, langs)["rows"]
            print(f"{name:40s} {variant:6s} {weighted(rows, 1, None):6.3f} {weighted(rows, 2, None):7.3f} "
                  f"{weighted(rows, 2, 'en'):10.3f} {weighted(rows, 2, 'ms'):10.3f}")  # fmt: skip
            details[name, variant] = rows

    for (name, variant), rows in details.items():
        if variant != "masked":
            continue
        print(f"\nweakest queries, {name} (city-masked, capped precision, companies):")
        scorable = [r for r in rows if not np.isnan(r[2])]  # singleton types have no same-type candidate
        for i, _, capped, weight, lang in sorted(scorable, key=lambda r: (r[2], -r[3]))[: args.worst]:
            print(f"  {capped:5.2f}  {weight:3d}  [{lang}] {types[i]:18s} {texts[i][:62]}")


if __name__ == "__main__":
    main()
