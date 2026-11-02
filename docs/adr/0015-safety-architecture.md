# ADR 0015 – Safety Architecture

**Date:** 2026-11-02  
**Status:** Accepted  
**Deciders:** Mouhamed SARR

---

## Context

Explorai lets users upload private documents and ask questions against them.
Two classes of safety risk emerged during sub-project 4 design:

1. **Prompt injection / adversarial input** – a question crafted to override
   the system prompt, exfiltrate chunks, or make the model produce harmful
   output.
2. **PII in the corpus** – a document the user uploaded may contain personal
   data (email addresses, phone numbers, names) that the pipeline would index
   and the model would quote verbatim in answers.

Both must be addressed before the chat UI ships (sub-project 6).

---

## Decision

### Input filter (injection defence)

A two-phase cascade runs on every question before retrieval:

**Phase 1 – regex patterns** (fast, deterministic, no network call).
Eleven patterns cover the most common injection classes: system-prompt
override, ignore-previous-instructions, role-play, output format override,
jailbreak markers (`DAN`, `JAILBREAK`, `SUDO`), credential/token exfiltration,
code-execution requests, tool-call injection, and HTML/markdown injection.
The question is NFKC-normalised before matching to defeat homoglyph tricks.

**Phase 2 – LLM-as-judge** (only when Phase 1 passes).
The generator is asked a binary question in a dedicated prompt; an exact
`YES` in the first token blocks the question.  Any other response allows it,
making the filter fail-open rather than fail-closed on a provider outage.

Rejected options:

- *Phase 1 only* – misses semantically adversarial inputs that use no
  keyword.
- *LLM-as-judge only* – slow, expensive, and provider-dependent for every
  request.
- *Allow-list* – far too restrictive for a general-purpose Q&A tool.

### Output filter (PII / residual marker scan)

After citation verification, the generated answer and citation quotes are
scanned for PII and for residual `[REDACTED_*]` markers.  If either is
found, the answer is suppressed and a refusal is returned.

PII detection uses **presidio-analyzer** when installed, with a **regex
fallback** (EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, SSN, IP_ADDRESS,
PERSON) that is always active.  Presidio is an optional extras group
(`[pii]`) so CI runs without it; the regex fallback covers the annotated
test cases.

Rejected options:

- *Redact before returning* – a redacted answer is often useless; a
  clear refusal is more honest.
- *Skip output scan* – a retrieval hit on a document whose PII was not
  caught at ingestion would leak it through the answer.

### Ingestion PII hook

`chunk_pending_versions` accepts an optional `PiiScanner`; when provided it
scans each version's concatenated section text before chunking:

- `pii_action="block"` (default): `Document.pii_blocked` is set to `True`
  and the version is skipped.  Blocked documents are excluded from future
  `versions_to_chunk` queries.
- `pii_action="redact"`: the full redacted text replaces the sections as a
  single section, and chunking proceeds normally.

The `pii_action` value is set at upload time via the `?pii_action=` query
parameter (default `"block"`).

### Document model

Three columns added to `documents` (migration 0006):

| Column | Type | Default | Meaning |
|---|---|---|---|
| `pii_action` | `varchar(16)` | `"block"` | What the pipeline does on PII detection |
| `pii_redacted` | `boolean` | `false` | True when the pipeline redacted sections |
| `pii_blocked` | `boolean` | `false` | True when the pipeline blocked the document |

`pii_blocked` is exposed on `DocumentOut` so the frontend can show a banner.

---

## Consequences

- Every question incurs one regex scan and, when it passes, one LLM call.
  At 30 questions/hour/user (the current rate limit) the overhead is
  acceptable.
- The LLM judge adds latency proportional to the provider's TTFT.  A
  provider outage makes Phase 2 a no-op (fail-open), not a hard block.
- Redacted ingestion collapses multi-section structure into one blob.
  Chunk quality degrades for heavily-PII documents; blocking is the
  safer default.
- The regex fallback does not match PII that appears only in context (e.g.
  "the person in question").  Presidio's NLP models catch more; the regex
  fallback is a floor, not a ceiling.
- Private text is sent to Voyage (embedding) and Anthropic (generation).
  This is documented in ADR 0014 and accepted by the maintainer.
