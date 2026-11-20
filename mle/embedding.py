"""Embedders: turn text into 384-d unit vectors. The schema fixes `vector(384)`.

`embed` raises ValueError for a text it cannot handle (the write path rejects that one message); any other
exception means infrastructure trouble (model missing, process down) and the batch is retried, not rejected.
"""

import hashlib
import math
import re
from collections.abc import Sequence
from itertools import pairwise
from typing import Protocol

from mle.config import Settings

DIM = 384
_WORDS = re.compile(r"\w+")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> Sequence[Sequence[float]]: ...


class HashingEmbedder:
    """Placeholder for tests/offline: signed hashed unigrams + bigrams, L2-normalised. Matches exact words only,
    knows no synonyms and no Malay/English link. Deterministic: uses blake2b, never Python's randomised hash()."""

    name = "hashing-384-v1"
    dim = DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        words = _WORDS.findall(text.lower())
        features = words + [f"{a} {b}" for a, b in pairwise(words)]
        vec = [0.0] * DIM
        for feature in features:
            h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
            vec[h % DIM] += 1.0 if (h >> 32) & 1 else -1.0  # a different bit than the slot picks the sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:  # empty text, or hashed features cancelled out: a zero vector breaks cosine search
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]


class FastEmbedEmbedder:
    """Real model through fastembed (ONNX, CPU). The model is downloaded once and cached; the import is lazy so
    unit tests and the hashing path never need it."""

    dim = DIM

    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        self.name = model_name
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = [v.tolist() for v in self._model.embed(texts)]
        for v in vectors:
            if len(v) != DIM:
                raise RuntimeError(f"{self.name} outputs {len(v)} dimensions, the schema needs {DIM}")
        return vectors


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "hashing":
        return HashingEmbedder()
    if settings.embedder == "fastembed":
        return FastEmbedEmbedder(settings.embedding_model)
    raise ValueError(f"unknown embedder {settings.embedder!r}: use 'fastembed' or 'hashing'")
