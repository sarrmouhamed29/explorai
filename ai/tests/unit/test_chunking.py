import pytest

from explorai_ai.chunking import ChunkText, SectionInput, chunk_sections, embedding_text


def words(n: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def run(sections: list[SectionInput], max_chunks: int = 500) -> list[ChunkText]:
    return chunk_sections(
        sections, target_words=10, overlap_words=3, max_chunks=max_chunks
    )


def test_chunks_never_cross_a_section() -> None:
    chunks = run([SectionInput("A", words(12, "a")), SectionInput("B", words(12, "b"))])
    for chunk in chunks:
        prefixes = {w[0] for w in chunk.text.split()}
        assert len(prefixes) == 1


def test_overlap_is_shared_between_consecutive_windows() -> None:
    chunks = run([SectionInput(None, words(20))])
    first, second = chunks[0].text.split(), chunks[1].text.split()
    assert first[-3:] == second[:3]
    assert second[0] == "w7"


def test_last_window_may_be_shorter() -> None:
    chunks = run([SectionInput(None, words(12))])
    assert [c.word_count for c in chunks] == [10, 5]


def test_no_window_made_only_of_overlap() -> None:
    chunks = run([SectionInput(None, words(10))])
    assert [c.word_count for c in chunks] == [10]


def test_tiny_sections_are_skipped() -> None:
    chunks = run([SectionInput("t", "one two three four"), SectionInput("u", words(6))])
    assert len(chunks) == 1
    assert chunks[0].heading == "u"


def test_max_chunks_truncates() -> None:
    chunks = run([SectionInput(None, words(100))], max_chunks=3)
    assert len(chunks) == 3


def test_ordinals_continue_across_sections() -> None:
    chunks = run([SectionInput("A", words(12, "a")), SectionInput("B", words(12, "b"))])
    assert [c.ordinal for c in chunks] == [0, 1, 2, 3]


def test_embedding_text_prefixes_the_heading() -> None:
    assert embedding_text("Title", "body") == "Title\nbody"
    assert embedding_text(None, "body") == "body"


def test_empty_input_gives_no_chunk() -> None:
    assert run([]) == []
    assert run([SectionInput("A", "   ")]) == []


@pytest.mark.parametrize(("target", "overlap"), [(10, 10), (10, 11), (0, 0), (10, -1)])
def test_invalid_window_settings_are_rejected(target: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="overlap"):
        chunk_sections(
            [SectionInput(None, words(20))],
            target_words=target,
            overlap_words=overlap,
            max_chunks=5,
        )
