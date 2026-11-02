import pytest

from explorai_pipelines.extract.html import HTML_EXTRACTOR_VERSION, extract_html
from explorai_pipelines.extract.models import Unextractable

PARAGRAPH = (
    "Retrieval augmented generation combines a search step with a language "
    "model so that answers can cite the documents they were built from. "
    "This paragraph is long enough for the content extractor to keep it."
)

PAGE = f"""<!doctype html>
<html><head><title>Notes on RAG</title></head>
<body>
<nav><a href="/">Home</a> <a href="/about">About us</a> <a href="/login">Login</a></nav>
<article>
  <h1>Notes on RAG</h1>
  <p>{PARAGRAPH}</p>
  <h2>Why it works</h2>
  <p>{PARAGRAPH}</p>
  <p>{PARAGRAPH}</p>
</article>
<footer>Copyright navigation junk, cookie settings, subscribe to our newsletter</footer>
</body></html>
""".encode()


def test_extractor_version_is_pinned() -> None:
    assert HTML_EXTRACTOR_VERSION == "html-1"


def test_html_main_content_without_navigation() -> None:
    document = extract_html(PAGE, url="https://example.org/rag")
    text = "\n".join(s.text for s in document.sections)

    assert "Retrieval augmented generation" in text
    assert "Login" not in text
    assert "newsletter" not in text


def test_html_headings_become_sections_and_title_is_read() -> None:
    document = extract_html(PAGE, url="https://example.org/rag")

    assert document.title == "Notes on RAG"
    assert "Why it works" in [s.heading for s in document.sections]


def test_html_without_content_unextractable() -> None:
    with pytest.raises(Unextractable):
        extract_html(b"<html><body><nav>menu</nav></body></html>")


def test_non_html_bytes_unextractable() -> None:
    with pytest.raises(Unextractable):
        extract_html(b"\x00\x01\x02 binary")
