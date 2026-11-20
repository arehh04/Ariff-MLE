import math

import pytest

from mle.config import Settings
from mle.embedding import DIM, EmbedderUnavailable, FastEmbedEmbedder, HashingEmbedder, make_embedder


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))  # vectors are unit length


def test_deterministic_and_right_size():
    e = HashingEmbedder()
    first, second = e.embed(["Boutique hotel in Ipoh"]), e.embed(["Boutique hotel in Ipoh"])
    assert first == second
    assert len(first[0]) == DIM == e.dim == 384


def test_unit_length():
    for text in ["Boutique hotel in Ipoh", "a", "x " * 500]:
        assert math.isclose(
            math.sqrt(sum(v * v for v in HashingEmbedder().embed([text])[0])), 1.0, abs_tol=1e-9
        )


def test_related_text_is_closer_than_unrelated():
    a, b, c = HashingEmbedder().embed(
        ["Boutique hotel in Ipoh", "Small hotel in Ipoh city", "Oil palm plantation and milling"]
    )
    assert cosine(a, b) > cosine(a, c)


@pytest.mark.parametrize("text", ["", "   ", "!!!"])
def test_empty_text_is_still_a_valid_unit_vector(text):
    (vec,) = HashingEmbedder().embed([text])
    assert len(vec) == DIM and math.isclose(sum(v * v for v in vec), 1.0, abs_tol=1e-9)


def test_one_word_sets_exactly_one_slot():
    assert sum(1 for v in HashingEmbedder().embed(["hotel"])[0] if v) == 1  # one unigram, no bigram


def test_make_embedder_selects_by_name():
    assert isinstance(make_embedder(Settings(embedder="hashing")), HashingEmbedder)


def test_unknown_embedder_fails_loudly():
    with pytest.raises(ValueError, match="nope"):
        make_embedder(Settings(embedder="nope"))


def test_settings_honour_compose_ports(monkeypatch):
    monkeypatch.setenv("POSTGRES_PORT", "5544")
    monkeypatch.setenv("RABBITMQ_PORT", "5699")
    s = Settings()
    assert ":5544/companies" in s.database_url and ":5699/" in s.rabbitmq_url


def test_mle_prefix_overrides(monkeypatch):
    monkeypatch.setenv("MLE_EMBEDDER", "hashing")
    assert Settings().embedder == "hashing"


def test_model_failure_is_unavailable_but_a_wrong_dimension_is_a_plain_error():
    """A crashing model is infrastructure (retry); a model with the wrong output size is a misconfiguration (fail fast)."""
    import numpy as np

    class Boom:
        def embed(self, texts):
            raise ValueError("onnx exploded")

    class TooShort:
        def embed(self, texts):
            return [np.zeros(3) for _ in texts]

    embedder = FastEmbedEmbedder.__new__(FastEmbedEmbedder)  # skip loading the real model
    embedder.name = "stub"
    embedder._model = Boom()
    with pytest.raises(EmbedderUnavailable):
        embedder.embed(["x"])
    embedder._model = TooShort()
    with pytest.raises(RuntimeError) as caught:
        embedder.embed(["x"])
    assert not isinstance(caught.value, EmbedderUnavailable)
