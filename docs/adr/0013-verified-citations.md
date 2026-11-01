# 0013. Citations are verified by our code, not trusted

- Status: Accepted
- Date: 2026-11-01

## Context

The success criterion of sub-project 3 is an assistant that "answers with verifiable sources and refuses to answer when the corpus lacks the information". A language model asked to cite will usually do so, but asking is not proof: it can paraphrase and present the paraphrase as a quote, cite the wrong source number, or invent a passage. Document text is also attacker-controlled (anyone can upload a PDF), so a prompt injection inside a chunk can push the model to cite anything. A citation that nobody checked is decoration.

## Decision

The model must answer through a forced tool call (`answer_question`, see ADR [0014](0014-providers-behind-interfaces.md)) whose input holds `status`, `answer` (text with `[n]` markers) and `citations`, a list of `{source: n, quote: "..."}`. Our code then checks every citation (`explorai_ai.citations.verify_citations`) against the chunk it names, as it was re-read from PostgreSQL under RLS:

1. **The source number must be in range** (1-based position among the sources of the prompt, at most 8).
2. **The quote is normalised** the same way as the chunk: Unicode NFKC (ligatures, non-breaking spaces and full-width forms become their plain equivalents), case-folded, every whitespace run collapsed to one space. Quotation marks and ellipses that the model wraps around a quote are stripped, at the two ends only.
3. **A floor: at least 2 words and 10 characters** after normalisation. Without it, a quote such as "the" or "and the" occurs in almost every chunk and would turn any answer into a "verified" one (the first plan said 3 characters; the review raised it).
4. **The normalised quote must be a substring of the normalised chunk text.** No fuzzy matching: a dropped hyphen, an added word or an inner "..." stitching two passages fails.
5. Repeats of the same (source, quote) are kept once.

A failing citation is removed. **If the model said `answered` and no citation survives, the answer becomes a refusal** (`insufficient_evidence`, the third refusal level). The quote returned to the client is the model's own text with whitespace collapsed and edge marks stripped.

## What the check proves, and what it does not

It proves that **each returned quote occurs, up to case, whitespace and Unicode compatibility forms, in a chunk the user is allowed to read**, from the latest canonical version of its document, and that this chunk was among the sources given to the model.

It does **not** prove:

- **That the answer is supported.** The check looks at quotes, not claims. A real quote can be attached to a claim it does not support, or to a wrong conclusion. Judging entailment needs an evaluator (an LLM judge or a natural-language-inference model), which is sub-project 4.
- **That the `[n]` markers in the answer text match the surviving citations.** The answer text is returned as the model wrote it; when some citations are dropped and others survive, a marker in the text can point at a citation that was removed.
- **That the quote shown is the source's exact span.** It is the model's text, matched after normalisation, so its case, ligatures or compatibility characters may differ from the stored text. For example, a chunk extracted with the ligature `ﬁ` (U+FB01) in "ﬁne-tuning" matches the model's "Fine-tuning", and the client sees the model's spelling; a superscript `10⁻³` becomes `10−3` under NFKC (with U+2212, the minus sign), so it matches a quote written with that minus sign but not one written with an ASCII hyphen. A client that highlights the quote in the source must search with the same normalisation.
- **That short quotes are meaningful.** Two words and ten characters ("of the model") still occur in many chunks. The floor stops the trivial cases, not every weak citation.

Known limits of the matching itself:

- **Scripts written without spaces.** The 2-word floor counts whitespace-separated words, so a Chinese or Japanese quote without spaces is one "word" and is always refused. Documents in those languages cannot yet produce a verified answer.
- **Short factual quotes are refused.** "42%" or "GPT-4" alone fail the floor; the model has to quote a longer span.
- **NFKC and case folding fold more than the eye notices** (for example `ﬁ` and `fi`, full-width and ASCII digits), and less than a reader might expect (a superscript minus becomes U+2212, not `-`). That is the intent, but it means "verbatim" is "verbatim after normalisation".

## Alternatives considered

- **Trust the model's citations.** No code, no guarantee. Rejected: it fails the success criterion.
- **Fuzzy matching (edit distance or token overlap thresholds).** Tolerates OCR noise and small model slips, but also lets paraphrases and plausible inventions through, and the threshold is arbitrary. Rejected for now; it can be measured in sub-project 4.
- **Character offsets or sentence ids.** The model cites sentence numbers or offsets of a pre-segmented source and the code extracts the span itself, so the shown quote is exactly the source's. Models are poor at counting characters; sentence ids are more robust but enlarge the prompt. A credible next step.
- **The provider's own citation feature.** Anthropic's API can return cited spans when sources are sent as document blocks. Not evaluated here: it ties the check to one vendor, and the fakes would have to imitate it. Worth comparing later.
- **An LLM judge checking support.** Catches unsupported claims, which our check cannot, but costs a second call per answer and is itself fallible. Belongs to the evaluation sub-project.

## Consequences

- A hallucinated or misattributed quote never reaches the user as a citation, and an answer without any verifiable citation is a refusal.
- Some correct answers are refused (a model that paraphrases instead of quoting, CJK text, very short quotes). The fake-provider plumbing run of `measure.py` refuses 6 of its 10 answerable questions; those numbers come from deterministic word-overlap fakes on a tiny in-memory corpus and say nothing about real models. The real false-refusal rate is unknown until the maintainer runs the measurement (`docs/evaluation/rag-baseline.md`).
- The check is cheap (string operations on at most 8 chunks) and fully unit-tested, with mutation proofs recorded in the task reviews.
