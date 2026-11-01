# RAG baseline

**Status: no real-provider measurement has been run yet.** The measurement needs the maintainer's Voyage and Anthropic keys and a populated index, and costs money; the agents that wrote it never had keys and never ran it. This file holds the method, the questions to review and an empty results section. There is no number here, and none must be quoted until the section "Results" is filled by a real run.

## What is measured

Fourteen hand-annotated questions over the three annotated arXiv papers of sub-project 1 (`pipelines/tests/fixtures/annotated/`, the same sample as `extraction-baseline.md`). They live in `ai/tests/fixtures/questions/questions.json`:

```json
{"question": "...", "paper": "2610.40577", "expected_quote": "verbatim sentence from the paper"}
```

- **10 answerable questions**, each with a verbatim quote of the paper that answers it.
- **4 unanswerable questions** (`expected_quote: null`, `paper: "none"`) on topics none of the three papers covers (football, positional encodings, image diffusion, diabetes). The corpus must not answer them, so the right behaviour is an `insufficient_evidence` refusal.

| Metric | Over | Definition |
|---|---|---|
| Retrieval hit rate | answerable | the normalised quote occurs in at least one of the (at most 8) chunks the generator is shown |
| Correct refusal rate | unanswerable | the pipeline answers `insufficient_evidence` |
| False refusals | answerable | questions refused although they should have been answered (reported as a count and a rate) |

"Normalised" is the citation verifier's own rule (`normalise_for_match`: Unicode NFKC, case-folded, whitespace collapsed), so the metric and the product agree on what "the text contains this quote" means.

The pipeline hides the kept chunks (its result only has verified citations), so `measure.py` wraps the retriever to record them instead of changing the pipeline. Questions are asked on the `public` scope with a fixed placeholder user, so the measurement only sees the public corpus.

## How to run

The unit tests (no network, no key) check the scoring, the report and the question file, including that every quote really occurs in one chunk of its paper, chunked with the production settings:

```bash
uv run pytest ai/tests/unit/test_measure.py --no-cov
```

A plumbing check with the fake providers on a tiny in-memory corpus (the numbers mean nothing):

```bash
uv run python -X utf8 -m explorai_ai.measure --provider fake
```

The real measurement, to run by the maintainer:

1. Start the stack with the real keys exported (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `RAG_PROVIDER=real`, `QDRANT_URL`, `QDRANT_API_KEY`, `DATABASE_URL`). Never put the keys in a committed file.
2. Put the three papers in the **public** corpus and index them (`index-pending` of the `pipelines` service, see `pipelines/README.md`). Check that the three versions are indexed before measuring; the command only checks that the collection of the configured model exists. This step has not been exercised end to end by the author.
3. Run:

```bash
uv run python -X utf8 -m explorai_ai.measure --provider real --output docs/evaluation/rag-baseline-run.md
```

Without the keys, the model's Qdrant collection or `DATABASE_URL` the command stops with a message and exit code 2, before any provider call. A provider outage stops it with exit code 1.

## Questions to review (by the maintainer)

The author wrote these questions and picked the quotes by reading the papers; they are **not validated by a human**. Review each one: is the question answerable by the quote alone, is the quote the best evidence, is it unambiguous? A quote may also occur elsewhere in the paper (the metric accepts any chunk that contains it).

| # | Paper | Question | Expected quote |
|---|---|---|---|
| 1 | 2610.40577 | By how many percentage points does full Multi-View MoE improve the mean score over the baseline? | full Multi-View MoE improves the mean score across nine downstream tasks by 1.18 percentage points over the baseline |
| 2 | 2610.40577 | Why does Multi-View MoE keep the same active-expert count and communication volume as a standard MoE? | Each selected expert executes one forward pass, preserving the active-expert count, expert FLOPs, and all-to-all communication volume. |
| 3 | 2610.40577 | How does the Multi-Router submodule combine its routing heads before selecting experts? | independently initialized sigmoid routing heads produce scores that are summed before one top-k selection |
| 4 | 2610.40577 | What do routing scores decide in a sparse mixture-of-experts model? | Routing scores determine which experts process each token and how their outputs are combined. |
| 5 | 2610.40619 | How many hypotheses does the Strata benchmark contain and what data are they about? | 8,735 hypotheses over the clinical timelines of vaccine adverse-event reports |
| 6 | 2610.40619 | What is the Art paradigm proposed for temporal hypothesis verification? | Art (articulate, retrieve, test), a staged paradigm that exposes each decision as an inspectable output |
| 7 | 2610.40619 | At which decision do systems fail most on the Strata benchmark? | systems fail mostly at choosing which records count |
| 8 | 2610.40627 | In which part of a response does safety alignment concentrate, according to the ShallowDefend paper? | Safety alignment is known to be shallow: an aligned model departs from its base almost exclusively over the first few output tokens |
| 9 | 2610.40627 | How well protected are language models against multi-turn attacks compared with single-turn ones? | Multi-turn dialogue, however, remains far less protected. |
| 10 | 2610.40627 | Why does a response that opens compliantly tend to stay compliant? | once a response opens compliantly the aligned distribution collapses back to the base model |
| 11 | none | Who won the 2022 FIFA World Cup final? | (none: must be refused) |
| 12 | none | How does the original Transformer define its sinusoidal positional encodings? | (none: must be refused) |
| 13 | none | Which diffusion model architecture is best for high-resolution image generation? | (none: must be refused) |
| 14 | none | What are the first-line treatments for type 2 diabetes? | (none: must be refused) |

Known weak points of the set: question 4 is generic (the quote is an introductory sentence several chunks could resemble); question 9 is answered by a very short sentence; the four unanswerable questions are far from the corpus, which is the easy case for the refusal threshold (a near-topic unanswerable question, such as one about a method the papers only cite, would be a harder and more informative test).

## Limits of the metric

- **It measures evidence recall, not answer quality.** A hit means the right sentence was in the sources shown to the model. It does not say the model used it, answered correctly or cited it; a miss does not mean the answer is wrong (another chunk may say the same thing).
- **One verbatim quote per question** is a narrow ground truth: a question answered equally well by another passage counts as a miss.
- **Small sample** (10 and 4 questions): one question moves a rate by 10 or 25 points. The result says "no obvious failure", not "reliable".
- **Refusals are checked on the status only.** An unanswerable question counts as correct when refused, whatever the reason (threshold, model, or no verified citation).
- **Generation is not deterministic**: two real runs can differ. Run it more than once before comparing two configurations.
- The questions were written by the same assistant that built the pipeline, so they may be easier than a stranger's.

## Results

Not measured yet. After the first real run, record here: the date, the models (`generation_model`, `rewrite_model`, `embedding_model`, `rerank_model` and the refusal threshold in use), the commit, and the table printed by the command. Then use it as the reference that later changes (chunk size, threshold, rewriting on or off) must beat.
