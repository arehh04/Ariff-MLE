"""Offline retrieval eval for the deep dive: which embedding model finds same-industry companies across English and Malay?

Ground truth comes from the feed itself (no database needed): every distinct description maps to exactly one `industry`
(verified when this script was written). For each description we rank the OTHER descriptions by cosine similarity and
report precision@k = share of the top-k that share the query's industry.

  cross  = candidates are in the other language (English query -> Malay candidates, and the reverse)
  mono   = candidates are in the same language
  masked = city names replaced by CITY first, so shared tokens like "Ipoh" cannot give a model free credit

Run: uv run python scripts/eval_embeddings.py [--models name ...] [--cuda]
Heuristic limits: the Malay/English split is a keyword list, and industry match is a proxy for "similar company".
"""

import argparse
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from mle.embedding import HashingEmbedder
from mle.normalize import Event, parse_message

FEED = Path(__file__).resolve().parents[1] / "data" / "feed.jsonl"
K = 5
MALAY = re.compile(
    r"\b(di|dan|untuk|dengan|yang|kepada|dari|serta|ke|dalam|seluruh|perkhidmatan|pembuatan|penghasilan|kedai|"
    r"pengeluaran|pembekal|pembinaan|penghantaran|perniagaan|bungkusan|produk|tadika|taska|ladang|kilang|"
    r"pengangkutan|pengedaran|pelancongan|restoran|klinik|pembangunan|perisian|perakaunan|swasta)\b",
    re.IGNORECASE,
)
CITY = re.compile(
    r"\b(Ipoh|Johor Bahru|Kota Kinabalu|Kuantan|Kuching|Melaka|Penang|Seremban|Port Klang|"
    r"(?:the )?Klang Valley|Lembah Klang|Kuala Lumpur)\b",
    re.IGNORECASE,
)
# name -> (fastembed model id, text prefix). e5 models are trained with a "query: " prefix for symmetric similarity.
MODELS: dict[str, tuple[str, str]] = {
    "bge-small-en-v1.5": ("BAAI/bge-small-en-v1.5", ""),
    "paraphrase-multilingual-MiniLM-L12-v2": (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "",
    ),
    "multilingual-e5-large": ("intfloat/multilingual-e5-large", "query: "),
}


def load_descriptions() -> tuple[list[str], list[str]]:
    """Distinct descriptions and their industry, read from the feed with the real parser."""
    industries: dict[str, Counter] = defaultdict(Counter)
    for line in FEED.read_text(encoding="utf-8").splitlines():
        event = parse_message(None, line.encode())
        if isinstance(event, Event) and event.nature_of_business and event.industry:
            industries[event.nature_of_business][event.industry] += 1
    texts = sorted(industries)
    return texts, [industries[t].most_common(1)[0][0] for t in texts]


def dedupe(texts: list[str], labels: list[str]) -> tuple[list[str], list[str]]:
    seen: dict[str, str] = {}
    for t, label in zip(texts, labels, strict=True):
        seen.setdefault(t, label)
    return list(seen), list(seen.values())


def precision_at_k(vecs: np.ndarray, labels: list[str], malay: list[bool], cross: bool) -> float:
    sims = vecs @ vecs.T
    scores = []
    for i in range(len(labels)):
        candidates = [j for j in range(len(labels)) if j != i and (malay[j] != malay[i]) == cross]
        if not candidates:
            continue
        top = sorted(candidates, key=lambda j: -sims[i, j])[:K]
        scores.append(sum(labels[j] == labels[i] for j in top) / len(top))
    return float(np.mean(scores))


def evaluate(embed, texts: list[str], labels: list[str]) -> dict[str, float]:
    result = {}
    for condition, (t, lab) in {
        "raw": (texts, labels),
        "masked": dedupe([CITY.sub("CITY", x) for x in texts], labels),
    }.items():
        vecs = np.asarray(embed(t), dtype=np.float64)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        malay = [bool(MALAY.search(x)) for x in t]
        result[f"cross {condition}"] = precision_at_k(vecs, lab, malay, cross=True)
        result[f"mono {condition}"] = precision_at_k(vecs, lab, malay, cross=False)
        result[f"n {condition}"] = len(t)
    return result


def fastembed_embedder(model_id: str, prefix: str, cuda: bool):
    from fastembed import TextEmbedding

    started = time.perf_counter()
    model = TextEmbedding(model_name=model_id, cuda=cuda) if cuda else TextEmbedding(model_name=model_id)
    load_seconds = time.perf_counter() - started
    return (lambda texts: [v for v in model.embed([prefix + t for t in texts])]), load_seconds


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--models", nargs="*", default=["hashing", *MODELS])
    parser.add_argument(
        "--cuda", action="store_true", help="run fastembed on the GPU (needs onnxruntime-gpu)"
    )
    args = parser.parse_args()

    texts, labels = load_descriptions()
    malay = sum(bool(MALAY.search(t)) for t in texts)
    print(f"{len(texts)} distinct descriptions ({malay} Malay-looking, {len(texts) - malay} English-looking), "
          f"{len(set(labels))} industries, precision@{K}, device={'cuda' if args.cuda else 'cpu'}\n")  # fmt: skip
    masked_n = len(dedupe([CITY.sub("CITY", x) for x in texts], labels)[0])
    print(f"city-masked set (duplicates collapsed): {masked_n} descriptions\n")
    header = f"{'model':40s} {'dim':>4s} {'cross raw':>9s} {'cross masked':>12s} {'mono raw':>8s} {'mono masked':>11s} {'load s':>7s} {'embed s':>8s}"
    print(header + "\n" + "-" * len(header))
    for name in args.models:
        if name == "hashing":
            embed, load_seconds = HashingEmbedder().embed, 0.0
        else:
            model_id, prefix = MODELS[name]
            embed, load_seconds = fastembed_embedder(model_id, prefix, args.cuda)
        dim = len(embed(texts[:1])[0])
        r = evaluate(embed, texts, labels)
        # Embedding time for the raw condition only (169 texts), measured separately so metric work is excluded.
        timed = time.perf_counter()
        embed(texts)
        embed_seconds = time.perf_counter() - timed
        print(f"{name:40s} {dim:4d} {r['cross raw']:9.3f} {r['cross masked']:12.3f} {r['mono raw']:8.3f} "
              f"{r['mono masked']:11.3f} {load_seconds:7.1f} {embed_seconds:8.2f}")  # fmt: skip


if __name__ == "__main__":
    main()
