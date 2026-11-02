# 0012. Thin own RAG code instead of a framework

- Status: Accepted
- Date: 2026-11-01

## Context

A RAG pipeline is a short chain: chunk, embed, index, search, fuse, rerank, prompt, generate, check. Frameworks such as LlamaIndex and LangChain package every step behind their own abstractions (documents, nodes, retrievers, chains, vector store wrappers) and connect to many vendors. The master plan left the question open ("LangChain or LlamaIndex for RAG, spec 3").

Three requirements of this project weigh on the choice:

- **Owner-scoped retrieval.** Every search must carry the owner filter built by one function, and texts must be re-read from PostgreSQL under Row-Level Security (ADR [0011](0011-qdrant-and-retrieval-isolation.md)). A framework's vector-store wrapper usually returns the stored text with the hit and builds its own filters; bending it to "ids only, re-read under RLS" means working against it.
- **Deterministic tests without network or keys.** Every provider must be replaceable by a fake (ADR [0014](0014-providers-behind-interfaces.md)).
- **Explainability.** The maintainer must be able to explain every line in an interview.

## Decision

Write the pipeline ourselves, thinly, over the official clients: `qdrant-client`, the `anthropic` SDK, and `httpx` for Voyage's REST API. The whole `ai/` library is about 2,600 lines of Python including docstrings (`ai/src/explorai_ai`), each module one concern:

- `chunking` (word windows), `sparse` (BM25-style hashed sparse vectors), `scope` (the one filter builder), `index` (the Qdrant adapter), `store` (re-read under RLS), `retriever` (rewrite, hybrid search, `rrf_fuse` across queries, hydrate, rerank, degradations), `prompt` (tags, neutralisation, the answer tool schema), `citations` (verification), `pipeline` (refusal levels), `providers/` (ports and adapters), `fakes`, `retry`, `config`, `measure`.
- Orchestration stays in Prefect (`pipelines/`) and HTTP in FastAPI (`backend/`), as for the earlier sub-projects.

## Alternatives considered

- **LlamaIndex.** Fast to a demo (loaders, node parsers, retrievers, response synthesizers, citation query engines). Rejected because its abstractions (nodes carrying text in the vector store, its own metadata filters) fit poorly with "Qdrant holds no text, PostgreSQL decides visibility", and because the behaviour hides in layers that are hard to explain or to fake precisely.
- **LangChain.** Large ecosystem, but an API that changed often, and chains that are harder to replace by deterministic fakes than three small Protocols. LangGraph remains a candidate for the agents of sub-project 8, where a state machine with persistence is the actual need.
- **Haystack or another pipeline framework.** Same trade-off: components and wiring we would have to learn and adapt, for a chain short enough to write.
- **Qdrant's FastEmbed for embeddings and sparse vectors.** Convenient, but it downloads models on first use (tests, CI and containers would need them) and the embeddings come from the hosted provider anyway.

## Consequences

- Every behaviour that matters for safety is in our code and covered by unit tests with fakes: the filter, the re-read, the stale guard, fusion, refusal levels, citation checks, tag neutralisation, error mapping.
- **We own the code.** Features a framework gives for free must be written when needed: document loaders beyond our extractors, caching of embeddings or answers, streaming, conversation memory, tracing (Langfuse is sub-project 4). Bugs a framework's users would have found are ours to find.
- Vendor switches are cheap only behind our own interfaces: a new embedding or generation provider is one adapter class, but there is no catalogue of ready adapters.
- The code is small enough to read in an afternoon, which serves the learning goal and the interviews.
- If a later sub-project adopts a framework (LangGraph for agents), it should call this library as a tool rather than replace it, so the isolation guarantees stay in one place.
