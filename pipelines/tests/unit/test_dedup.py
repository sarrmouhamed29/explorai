from explorai_pipelines.dedup import NearDuplicateIndex, minhash_signature, text_hash

BASE = " ".join(f"word{i % 37}x{i}" for i in range(200))


def test_text_hash_ignores_whitespace_and_unicode_form() -> None:
    assert text_hash("ﬁne   text\n") == text_hash("fine text")


def test_text_hash_differs_for_different_text() -> None:
    assert text_hash("one") != text_hash("two")


def test_signature_is_deterministic() -> None:
    assert minhash_signature(BASE) == minhash_signature(BASE)


def test_near_duplicate_found() -> None:
    words = BASE.split()
    changed = words.copy()
    # One changed word alters 5 of ~196 shingles: Jaccard ~0.95, above 0.9.
    changed[100] = "changed"
    index = NearDuplicateIndex(threshold=0.9)
    index.add("original", minhash_signature(BASE))
    assert index.find(minhash_signature(" ".join(changed))) == "original"


def test_unrelated_text_not_found() -> None:
    other = " ".join(f"other{i}y{i * 7}" for i in range(200))
    index = NearDuplicateIndex(threshold=0.9)
    index.add("original", minhash_signature(BASE))
    assert index.find(minhash_signature(other)) is None


def test_empty_index_finds_nothing() -> None:
    assert NearDuplicateIndex().find(minhash_signature(BASE)) is None


def test_short_text_has_a_signature() -> None:
    assert minhash_signature("two words") != minhash_signature("")
