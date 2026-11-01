import uuid

import pytest

from explorai_ai.retriever import rrf_fuse, rrf_scores

A, B, C, D, E = (uuid.UUID(int=n) for n in (1, 2, 3, 4, 5))


def test_single_ranking_keeps_its_order() -> None:
    assert rrf_fuse([[C, A, B]]) == [C, A, B]


def test_scores_are_the_sum_of_reciprocal_ranks() -> None:
    scores = rrf_scores([[A, B, C], [B, C]], k=60)
    assert scores[A] == pytest.approx(1 / 61)
    assert scores[B] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[C] == pytest.approx(1 / 63 + 1 / 62)


def test_agreement_between_rankings_wins() -> None:
    # B is second and first; A is first once only; C is third and second.
    assert rrf_fuse([[A, B, C], [B, C]]) == [B, C, A]


def test_k_controls_how_much_the_top_rank_dominates() -> None:
    # A is first in one list; B is fourth in both.
    rankings = [[A, C, D, B], [C, D, E, B]]
    # Small k: a first place dominates (1/2 > 2/5).
    small = rrf_fuse(rankings, k=1)
    assert small.index(A) < small.index(B)
    # The usual k = 60: presence in several lists wins (1/61 < 2/64).
    usual = rrf_fuse(rankings)
    assert usual.index(B) < usual.index(A)


def test_ties_are_broken_by_id() -> None:
    # Same rank in two lists: equal scores, so the id's string order decides.
    assert rrf_fuse([[B], [A]]) == [A, B]
    assert rrf_fuse([[A], [B]]) == [A, B]


def test_a_repeated_id_counts_at_its_first_rank_only() -> None:
    assert rrf_scores([[A, B, A]], k=60) == {
        A: pytest.approx(1 / 61),
        B: pytest.approx(1 / 62),
    }


def test_no_rankings_or_empty_rankings_fuse_to_nothing() -> None:
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


def test_k_must_be_positive() -> None:
    with pytest.raises(ValueError):
        rrf_fuse([[A]], k=0)
