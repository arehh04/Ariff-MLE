import math

import pytest

from mle.embedding import DIM, HashingEmbedder

TEXTS = ["Boutique hotel in Ipoh", "Hotel butik di Ipoh", "Oil palm plantation", "x", "ab " * 300, "é à ü ñ",
         "日本語のテキスト", "🙂🙂🙂", "line\nbreak", "Tab\tsep", "UPPER lower MiXeD", "numbers 12345 67890"]  # fmt: skip


def norm(v) -> float:
    return math.sqrt(sum(x * x for x in v))


@pytest.mark.parametrize("text", TEXTS)
def test_every_text_gives_a_finite_unit_vector_of_the_right_size(text):
    (v,) = HashingEmbedder().embed([text])
    assert len(v) == DIM and all(math.isfinite(x) for x in v) and norm(v) == pytest.approx(1.0)


@pytest.mark.parametrize("text", TEXTS)
def test_batch_and_single_embedding_agree(text):
    e = HashingEmbedder()
    assert e.embed([text])[0] == e.embed(["filler", text, "another"])[1]


@pytest.mark.parametrize("text", ["Hotel butik di Ipoh", "UPPER lower"])
def test_case_and_whitespace_do_not_change_the_vector(text):
    e = HashingEmbedder()
    assert e.embed([text])[0] == e.embed([f"  {text.upper()}  "])[0] == e.embed([text.lower()])[0]


def test_word_order_matters_only_through_bigrams():
    a, b = HashingEmbedder().embed(["hotel butik ipoh", "ipoh butik hotel"])
    cosine = sum(x * y for x, y in zip(a, b, strict=True))
    assert 0.3 < cosine < 1.0  # shared unigrams, different bigrams
