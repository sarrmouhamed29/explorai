"""Measure PDF extraction quality on a hand-annotated sample.

    uv run python -m explorai_pipelines.measure [--output PATH]

Each ``<name>.pdf`` in the sample directory has a ``<name>.json`` annotation:

    {"order_pairs": [["fragment that comes first", "fragment that comes later"]],
     "repeated_lines": ["running header text"]}

Two rates are reported. ``column_order_rate``: share of annotated pairs where
both fragments are found and the first precedes the second (a mixed-up
two-column page breaks this). ``header_removal_rate``: share of annotated
running headers that no longer appear as a line of their own.
"""

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from explorai_pipelines.extract.pdf import PDF_EXTRACTOR_VERSION, extract_pdf

DEFAULT_SAMPLE_DIR = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "annotated"
)


@dataclass(frozen=True)
class FileScore:
    name: str
    pairs_ok: int
    pairs_total: int
    headers_removed: int
    headers_total: int


@dataclass(frozen=True)
class Report:
    extractor_version: str
    files: list[FileScore]

    @property
    def column_order_rate(self) -> float | None:
        return _rate(
            sum(f.pairs_ok for f in self.files), sum(f.pairs_total for f in self.files)
        )

    @property
    def header_removal_rate(self) -> float | None:
        return _rate(
            sum(f.headers_removed for f in self.files),
            sum(f.headers_total for f in self.files),
        )


def _rate(ok: int, total: int) -> float | None:
    return ok / total if total else None


def _collapse(text: str) -> str:
    return " ".join(text.split())


def score_order(text: str, pairs: Sequence[Sequence[str]]) -> tuple[int, int]:
    collapsed = _collapse(text)
    ok = 0
    for first, later in pairs:
        a, b = collapsed.find(_collapse(first)), collapsed.find(_collapse(later))
        if a >= 0 and b >= 0 and a < b:
            ok += 1
    return ok, len(pairs)


def score_headers(text: str, headers: Sequence[str]) -> tuple[int, int]:
    lines = {_collapse(line) for line in text.split("\n")}
    removed = sum(1 for header in headers if _collapse(header) not in lines)
    return removed, len(headers)


def measure_directory(directory: Path = DEFAULT_SAMPLE_DIR) -> Report:
    files: list[FileScore] = []
    for pdf in sorted(directory.glob("*.pdf")):
        annotation = json.loads(pdf.with_suffix(".json").read_text(encoding="utf-8"))
        document = extract_pdf(pdf.read_bytes())
        text = "\n".join(section.text for section in document.sections)
        pairs_ok, pairs_total = score_order(text, annotation.get("order_pairs", []))
        removed, headers_total = score_headers(
            text, annotation.get("repeated_lines", [])
        )
        files.append(FileScore(pdf.stem, pairs_ok, pairs_total, removed, headers_total))
    return Report(extractor_version=PDF_EXTRACTOR_VERSION, files=files)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def render_markdown(report: Report) -> str:
    rows = [
        f"| {f.name} | {f.pairs_ok}/{f.pairs_total} "
        f"| {f.headers_removed}/{f.headers_total} |"
        for f in report.files
    ]
    return "\n".join(
        [
            f"Extractor version: `{report.extractor_version}`",
            "",
            "| File | Pairs in order | Headers removed |",
            "|---|---|---|",
            *rows,
            f"| **Overall** | **{_percent(report.column_order_rate)}** "
            f"| **{_percent(report.header_removal_rate)}** |",
            "",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0] if __doc__ else None
    )
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR)
    parser.add_argument("--output", type=Path, help="also write the markdown here")
    args = parser.parse_args(argv)
    markdown = render_markdown(measure_directory(args.sample_dir))
    print(markdown)
    if args.output:
        args.output.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
