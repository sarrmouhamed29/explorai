import json
from pathlib import Path

from explorai_pipelines.extract.pdf import PDF_EXTRACTOR_VERSION
from explorai_pipelines.measure import (
    measure_directory,
    render_markdown,
    score_headers,
    score_order,
)
from explorai_pipelines.testing import make_pdf, two_column_page, with_running_header

TEXT = "first sentence ends here.\nthen the second\nsentence follows.\nlast one"


def test_pair_in_order_scores_one() -> None:
    assert score_order(TEXT, [["first sentence ends", "last one"]]) == (1, 1)


def test_reversed_pair_scores_zero() -> None:
    assert score_order(TEXT, [["last one", "first sentence ends"]]) == (0, 1)


def test_missing_fragment_counts_as_failed_pair() -> None:
    assert score_order(TEXT, [["first sentence ends", "never written"]]) == (0, 1)


def test_fragments_match_across_line_breaks() -> None:
    assert score_order(TEXT, [["second sentence follows.", "last one"]]) == (1, 1)


def test_header_absent_counts_as_removed() -> None:
    assert score_headers(TEXT, ["Running Header"]) == (1, 1)


def test_header_still_present_as_own_line_counts_as_kept() -> None:
    assert score_headers("body\nRunning Header\nbody", ["Running Header"]) == (0, 1)


def test_header_text_inside_a_longer_line_is_not_a_header() -> None:
    assert score_headers("see Running Header for details", ["Running Header"]) == (1, 1)


def _write_sample(directory: Path) -> None:
    left = "ALPHA starts the left column. " + "Filler words repeat here. " * 3
    right = "OMEGA ends the right column. " + "More filler words follow. " * 3
    pages = [
        with_running_header(
            two_column_page(left, right + str(i)), "Sample Journal", i + 1
        )
        for i in range(4)
    ]
    (directory / "sample.pdf").write_bytes(make_pdf(pages))
    (directory / "sample.json").write_text(
        json.dumps(
            {
                "order_pairs": [["ALPHA starts the left", "OMEGA ends the right"]],
                "repeated_lines": ["Sample Journal"],
            }
        )
    )


def test_measure_directory_scores_each_file_and_overall(tmp_path: Path) -> None:
    _write_sample(tmp_path)

    report = measure_directory(tmp_path)

    assert report.extractor_version == PDF_EXTRACTOR_VERSION
    assert [f.name for f in report.files] == ["sample"]
    assert report.column_order_rate == 1.0
    assert report.header_removal_rate == 1.0


def test_render_markdown_lists_files_and_overall_rates(tmp_path: Path) -> None:
    _write_sample(tmp_path)

    markdown = render_markdown(measure_directory(tmp_path))

    assert "| sample |" in markdown
    assert PDF_EXTRACTOR_VERSION in markdown
    assert "100%" in markdown
