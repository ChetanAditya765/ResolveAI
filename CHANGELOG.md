# Changelog

## 0.1.0 - 2026-09-08

First complete local release of the bounded repository-access workflow.

- FastAPI contracts, PostgreSQL models, migrations, seeded catalog, and policy corpus.
- LangGraph checkpoints, durable approval pause/resume, scoped tools and bounded recovery.
- RAG with configurable embeddings and deterministic checks against reviewed policy evidence.
- Simulated permission effects, idempotent execution and independent readback before closure.
- Next.js dashboard, ticket workflow, approvals, policy evidence and evaluations.
- Trace assertions and 37 isolated deterministic scenarios with fault injection.
- Docker Compose startup and backend, frontend and deployment CI definitions.
- An OpenAI adapter alongside explicit offline parser and lexical embedding providers.

## Public presentation - 2026-09-09

- Added a concise project overview, architecture visual and portfolio case study.
- Updated public documentation to describe the implemented reliability and security boundaries.
- Added a fresh [validation record](docs/release-validation.md); current numbers supersede older results.

Effects remain simulated. Demo identity selection is not production authentication. Live paid
model behavior and remote CI execution require separate verification.
