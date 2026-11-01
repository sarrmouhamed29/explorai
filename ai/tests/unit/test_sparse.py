import os
import subprocess

import pytest

from explorai_ai.sparse import encode_document, encode_query, tokenize

HELLO_INDEX = 3932620535  # blake2b(b"hello", digest_size=4), big-endian, computed once


def test_encoding_is_stable_across_processes() -> None:
    assert encode_document("hello").indices == [HELLO_INDEX]
    code = (
        "from explorai_ai.sparse import encode_document;"
        "print(encode_document('hello').indices[0])"
    )
    for seed in ("1", "2"):
        out = subprocess.run(
            ["uv", "run", "python", "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert int(out.stdout.strip()) == HELLO_INDEX


def test_repeated_token_saturates() -> None:
    once = encode_document("hello").values[0]
    ten = encode_document(" ".join(["hello"] * 10)).values[0]
    assert once == pytest.approx(1.0)
    assert once < ten < 2.2  # bounded by k1 + 1


def test_document_indices_are_sorted_and_unique() -> None:
    vector = encode_document("b a c a b zebra apple")
    assert vector.indices == sorted(set(vector.indices))
    assert len(vector.indices) == len(vector.values) == 5
    assert all(0 <= i < 2**32 for i in vector.indices)


def test_query_has_one_entry_per_distinct_token() -> None:
    vector = encode_query("cats and cats and dogs")
    assert len(vector.indices) == 3
    assert vector.values == [1.0, 1.0, 1.0]
    assert vector.indices == sorted(vector.indices)


def test_case_and_punctuation_are_ignored() -> None:
    assert encode_document("Hello, WORLD!") == encode_document("hello world")
    assert encode_query("Hello, WORLD!") == encode_query("world hello")


def test_empty_text_gives_empty_vector() -> None:
    assert encode_document("...").indices == []
    assert encode_query("").values == []


def test_accented_word_equals_its_unaccented_form() -> None:
    accented = "caf" + chr(0xE9)
    assert tokenize(accented) == ["cafe"]
    assert encode_document(accented) == encode_document("cafe")


def test_accented_word_is_one_token() -> None:
    assert tokenize(chr(0xE9) + "t" + chr(0xE9)) == ["ete"]
    assert tokenize("fran" + chr(0xE7) + "ais") == ["francais"]


def test_french_sentence_distinct_tokens() -> None:
    e = chr(0xE9)
    sentence = f"L{e}t{e} dernier, le fran" + chr(0xE7) + f"ais a visit{e} le caf{e}."
    assert len(tokenize(sentence)) == 8
    assert len(encode_query(sentence).indices) == 7  # "le" appears twice


def test_cjk_input_produces_tokens() -> None:
    assert (
        len(
            encode_document(
                chr(0x4F60) + chr(0x597D) + chr(0x4E16) + chr(0x754C)
            ).indices
        )
        >= 1
    )


def test_digits_and_mixed_alphanumerics_and_underscore() -> None:
    assert tokenize("GPT4 v2.0 snake_case") == ["gpt4", "v2", "0", "snake", "case"]
