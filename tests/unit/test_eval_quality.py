"""The deep-dive labels are rules, so the rules need a check: every feed description is labelled, in the right language."""

from collections import defaultdict

import pytest

from scripts.eval_quality import label, load


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Boutique hotel in Ipoh", ("hotel", "en")),
        ("Hotel butik di Ipoh", ("hotel", "ms")),
        ("Pengusaha homestay dan chalet tepi pantai", ("homestay", "ms")),
        ("Kebun durian dan eksport buah-buahan", ("durian", "ms")),
        ("Pengilangan bungkusan plastik untuk produk makanan", ("plastic-packaging", "ms")),  # not parcel
        ("Software development for small business accounting", ("software", "en")),  # not accounting
        ("Agensi penghantaran fret dan kastam", ("freight", "ms")),  # not parcel
    ],
)
def test_label_examples(text: str, expected: tuple[str, str]) -> None:
    assert label(text) == expected


def test_every_feed_description_is_labelled_and_malay_types_have_an_english_twin() -> None:
    langs: dict[str, set[str]] = defaultdict(set)
    for text in load():
        found = label(text)
        assert found is not None, text
        langs[found[0]].add(found[1])
    assert all("en" in seen for seen in langs.values() if "ms" in seen)
