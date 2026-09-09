# Release validation - 9 September 2026

Fresh local rerun against the public-preparation working tree. Application source was unchanged.
All runtime verification used `demo` and `local_hash`. No paid live-model request was made.

| Check | Current result | Scope |
| --- | --- | --- |
| Backend Ruff lint | Passed | `python -m ruff check backend` |
| Backend format | Passed; 115 files | `python -m ruff format --check backend` |
| Backend tests | **490 passed**, 1 upstream deprecation warning | Strict checkpoint serialization; PostgreSQL 16.15 and pgvector 0.8.6 included |
| Frontend lint / format | Passed | Existing lint and format-check scripts |
| Frontend typecheck | Passed | Next.js type generation and TypeScript |
| Frontend production build | Passed | Existing webpack build; Next.js standalone output |
| Chromium browser suite | **12 passed** | Real production frontend; isolated SQLite backend |
| Scenario evaluation CLI | **37/37 passed** | Real graph, deterministic fixtures and fault injection |
| HTTP recovery smoke | Passed | Approval wait, resume, verified resolution and persisted state across process restarts |
| Docker Compose configuration | Passed | `docker compose config --quiet` |
| Docker build / acceptance / container restart | **Blocked; not reverified** | Docker Desktop could not start its local Linux engine |

## Reproduce

With Python 3.12 dependencies installed and the environment activated, from the repository root:

```sh
python -m ruff check backend
python -m ruff format --check backend
python backend/scripts/verify_postgres.py --runtime-dir <installed-postgres-runtime>
python backend/scripts/smoke_http.py --agent
python -m app.evaluation
```

The PostgreSQL helper creates an isolated cluster, enables pgvector, runs all tests, stops the
server and removes its temporary cluster. This rerun set `LANGGRAPH_STRICT_MSGPACK=true`.
Alternatively set `TEST_DATABASE_URL` to a disposable PostgreSQL/pgvector database and run
`python -m pytest backend/tests -q`. Without PostgreSQL configuration the integration test skips.

From `frontend/`:

```sh
npm run lint
npm run format:check
npm run typecheck
npm run build
npm run test:e2e
```

Browser tests started fresh services on localhost ports 3011/8011. All 12 passed in approximately
one minute. PostgreSQL locking, vectors and migrations are covered separately by the backend suite.

## Evidence interpretation

The HTTP smoke confirmed a resolved ticket after approval and independent permission verification,
including recovery while waiting and persistence after subsequent process restart. This used
isolated SQLite files; it is **not** a fresh Docker/container restart result.

The 37 scenarios cover a fixed bounded repository-access domain. They verify observed traces and
explicit expectations, including safe escalation. Their score is not a measure of live-model
accuracy, semantic retrieval quality or universal enterprise safety.

Docker and Compose definitions are retained. Fresh image builds, full-stack container acceptance
and container restart persistence remain a release gate on a working Docker engine; earlier local
results are not republished as current evidence. See the [deployment runbook](deployment.md).
CI workflow definitions are present; no remote GitHub Actions success is claimed.

## Public assets

The architecture diagram describes source-level boundaries. Product screenshots use seeded
demonstration identities, simulated permissions and offline providers. They contain the product
viewport only. The concise public PDF is separate from detailed technical guides.
