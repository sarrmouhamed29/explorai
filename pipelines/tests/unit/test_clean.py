from explorai_pipelines.clean import (
    join_hyphenated,
    normalize,
    remove_repeated_lines,
)

LIGATURE_FI = chr(0xFB01)
NO_BREAK_SPACE = chr(0xA0)
NUL = chr(0)


def test_normalize_applies_nfkc_and_collapses_whitespace() -> None:
    raw = f"{LIGATURE_FI}ne   text{NO_BREAK_SPACE}here{NUL}\n\n\n\nnext"
    assert normalize(raw) == "fine text here\n\nnext"


def test_normalize_strips_line_edges() -> None:
    assert normalize("  a  \n  b  ") == "a\nb"


def test_join_hyphenated_word() -> None:
    assert join_hyphenated("infor-\nmation retrieval") == "information retrieval"


def test_hyphen_before_capital_or_digit_is_kept() -> None:
    assert join_hyphenated("Wang-\nLi method") == "Wang-\nLi method"
    assert join_hyphenated("table 3-\n4 rows") == "table 3-\n4 rows"


def _page(first: str, body: str, last: str) -> str:
    return f"{first}\n{body}\n{last}"


def test_repeated_header_and_page_number_removed() -> None:
    pages = [_page(f"Journal of X {10 + i}", f"body {i}", str(i + 1)) for i in range(4)]
    pages.append("Unique title line\nbody 4\nfooter")
    cleaned = remove_repeated_lines(pages)
    assert cleaned[0] == "body 0"
    assert cleaned[3] == "body 3"
    assert cleaned[4] == "Unique title line\nbody 4\nfooter"


def test_fewer_than_three_pages_left_untouched() -> None:
    pages = ["Header 1\nbody", "Header 2\nbody"]
    assert remove_repeated_lines(pages) == pages


def test_line_repeated_in_too_few_pages_is_kept() -> None:
    pages = [
        "Rare header\nbody a\nend a",
        "one\nbody b\nend b",
        "two\nbody c\nend c",
        "three\nbody d\nend d",
        "four\nbody e\nend e",
    ]
    assert remove_repeated_lines(pages) == pages
