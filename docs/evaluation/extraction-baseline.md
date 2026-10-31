# PDF extraction baseline

Measured on 2026-10-31 with extractor `pdf-1` (PyMuPDF, left-then-right column order, repeated header removal, heading-based sections). This is the starting point that sub-project 11 must beat with numbers.

## How to reproduce

```bash
uv run python -X utf8 -m explorai_pipelines.measure
```

The sample lives in `pipelines/tests/fixtures/annotated/`: three arXiv papers under the Creative Commons Attribution 4.0 licence (the licence was read on each abstract page) and one annotation file per paper. An annotation lists `order_pairs` (two short fragments of the paper, the first one coming before the second, often across a column break) and `repeated_lines` (running headers that must not remain in the text).

| Paper | Layout | Why it is in the sample |
|---|---|---|
| 2610.40577 | two columns, running header with a date, page numbers | column order across breaks, header removal |
| 2610.40619 | two columns, **two alternating** headers (odd and even pages), tables, algorithm boxes | the hard case for header removal |
| 2610.40627 | one column, "Preprint" header, page numbers | control: one column must stay correct |

## Results

| File | Pairs in order | Headers removed |
|---|---|---|
| 2610.40577 | 4/4 | 1/1 |
| 2610.40619 | 6/6 | 0/2 |
| 2610.40627 | 2/2 | 1/1 |
| **Overall** | **100%** (12/12) | **50%** (2/4) |

## Reading the numbers

- **Column order is solid on this sample** (12 of 12 annotated fragment pairs, including pairs that cross from the end of the left column to the top of the right one). The sample is small: 12 pairs say "no obvious failure", not "always right".
- **Header removal is the weak point.** Both headers of 2610.40619 survive. Two causes, found by inspecting the page text:
  1. The header `He et al.` is right-aligned, so the column logic classes it as right-column content and emits it after the whole left column. It is then neither the first nor the last line of the page, and the repeated-line rule (first or last line only) never sees it.
  2. The other header appears on only 5 of 12 pages (odd pages), below the 50% threshold of the repeated-line rule.
- Formulas, tables and algorithm boxes are extracted as plain text lines and are not scored here.

## Inputs for sub-project 11

1. Treat blocks in the top and bottom margin as full width before column assignment (fixes cause 1).
2. Detect headers that alternate between odd and even pages (fixes cause 2).
3. Score tables and formulas, which this baseline does not cover.
4. Grow the sample, and re-run this command after each extractor change, bumping `PDF_EXTRACTOR_VERSION` so the old versions stay comparable in the database.

## Caveats

The annotations were written by the assistant by reading the PDFs and must be reviewed by the maintainer before the numbers are quoted anywhere (for example in an interview or a README).
