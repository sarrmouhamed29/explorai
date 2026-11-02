import math

from explorai_ai.fakes import FakeEmbedder, FakeGenerator, FakeReranker
from explorai_ai.models import PromptSource


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_embedder_is_deterministic_and_normalised() -> None:
    embedder = FakeEmbedder(dimension=32)
    first = embedder.embed_query("Visa requirements for Japan")
    second = FakeEmbedder(dimension=32).embed_query("Visa requirements for Japan")
    assert first == second
    assert len(first) == 32
    assert math.isclose(math.sqrt(sum(x * x for x in first)), 1.0)
    assert embedder.model == "fake-embedding"
    assert embedder.embed_documents(["a b", "c"]) == [
        embedder.embed_query("a b"),
        embedder.embed_query("c"),
    ]


def test_fake_embedder_handles_text_without_words() -> None:
    vector = FakeEmbedder().embed_query("!!!")
    assert len(vector) == 64
    assert all(x == 0.0 for x in vector)


def test_related_texts_are_closer_than_unrelated() -> None:
    embedder = FakeEmbedder()
    query = embedder.embed_query("train tickets in Japan")
    related = embedder.embed_query("Buying train tickets in Japan is easy")
    unrelated = embedder.embed_query("Hiking boots for Patagonia glaciers")
    assert cosine(query, related) > cosine(query, unrelated)


def test_fake_reranker_orders_by_overlap() -> None:
    hits = FakeReranker().rerank(
        "train tickets japan",
        ["cooking pasta", "train tickets japan guide", "tickets only"],
        top_k=2,
    )
    assert [h.index for h in hits] == [1, 2]
    assert hits[0].score > hits[1].score


def test_fake_generator_answers_with_a_verbatim_quote() -> None:
    sources = [
        PromptSource(1, "Pasta", None, "Boil water. Add salt."),
        PromptSource(
            2, "Trains", "Tickets", "Japan rail passes save money. Book early."
        ),
    ]
    result = FakeGenerator().answer("Do Japan rail passes save money?", sources)
    assert result.status == "answered"
    assert result.answer == "According to [2]: Japan rail passes save money."
    assert len(result.citations) == 1
    citation = result.citations[0]
    assert citation.source == 2
    assert citation.quote == "Japan rail passes save money."
    assert citation.quote in sources[1].text


def test_fake_generator_refuses_without_overlap() -> None:
    sources = [PromptSource(1, None, None, "Boil water. Add salt.")]
    result = FakeGenerator().answer("quantum chromodynamics lattice", sources)
    assert result.status == "insufficient"
    assert result.citations == []
    assert FakeGenerator().answer("anything", []).status == "insufficient"


def test_fake_generator_rewrites_by_dropping_stop_words() -> None:
    queries = FakeGenerator().rewrite_queries("What are the Visa rules for Japan?", 3)
    assert queries == ["visa rules japan"]
