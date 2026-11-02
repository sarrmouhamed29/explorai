# 0002. FastAPI as the backend, not Next.js

- Status: Accepted
- Date: 2026-10-31

## Context

Explorai needs an API for authentication, document ingestion, RAG, agents and evaluation. The libraries for these (LangChain or LlamaIndex, LangGraph, Ragas, DeepEval, ML tooling) are Python-first. The frontend is a React single-page app with no server-side business logic.

## Decision

The backend is a Python service built with FastAPI and Pydantic. The frontend is a separate React + TypeScript app built with Vite. The two are linked by an OpenAPI contract: the backend exports `backend/openapi.json`, and the frontend generates its TypeScript types from it.

## Alternatives considered

- **Next.js as the backend** (route handlers or server actions). One language across the stack, but the AI ecosystem would have to be reached through a second runtime or reimplemented. It would also add a third service next to the Python AI code, which doubles the security surface (authentication, input validation and secrets in two places).
- **Next.js as a backend-for-frontend in front of FastAPI.** Rejected for the same reason: an extra service with no business logic of its own.
- **Django or Flask.** Both are viable Python choices. FastAPI was preferred for native async support, Pydantic validation and automatic OpenAPI generation, which the contract workflow relies on. (This preference is a judgement, not something measured in this repository.)

## Consequences

- Two languages in the repository (Python and TypeScript), kept consistent by the generated client and a CI drift check.
- Types are validated at the API boundary by Pydantic, and the frontend cannot call a route that does not exist in the contract without a type error.
- CORS is avoided in development by proxying API routes through the Vite dev server (see `frontend/vite.config.ts`).
- We give up server-side rendering and the Next.js full-stack conveniences; the application is a dashboard behind a login, so this costs little.
