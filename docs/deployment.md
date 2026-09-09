# Deployment and operations

ResolveAI v0.1 is a single-company demo application with a Next.js frontend, one FastAPI backend
process containing the leased agent worker, and PostgreSQL/pgvector. Migration and seed services
run once before application startup. Enterprise actions are simulated. Public authentication and
authorization across every read route are outside the current local demo scope.

## Start the demo with Docker

Install Docker Engine/Desktop with a current Compose plugin. From the repository root:

```powershell
docker compose config --quiet
docker compose up --build --wait --wait-timeout 180
docker compose ps -a
```

Open `http://localhost:3000`. API documentation is at `http://localhost:8000/docs`. Dependencies
start in order: healthy PostgreSQL → successful migrations → successful seed/index → healthy
backend → frontend. The configuration uses Docker's documented
[dependency conditions](https://docs.docker.com/compose/how-tos/startup-order/) and
[startup wait option](https://docs.docker.com/reference/cli/docker/compose/up/).

Default demo/lexical providers require no API key. Named-volume data survives a normal shutdown:

```powershell
docker compose down
```

Use a fresh named project for a disposable acceptance check if your normal demo already has
permission changes. The explicit project name takes precedence over the file's default name.
The same host ports must be free. Set offline providers before startup, since the seed job
embeds policies before the acceptance command can check readiness:

```powershell
$env:APP_ENV = 'test'
$env:LLM_PROVIDER = 'demo'
$env:EMBEDDING_PROVIDER = 'local_hash'
$env:DEMO_AUTH_ENABLED = 'true'
$env:AGENT_WORKER_ENABLED = 'true'
$env:AGENT_POLL_SECONDS = '0.1'
docker compose -p resolveai-acceptance up --build --wait --wait-timeout 180
.\.venv\Scripts\python.exe backend/scripts/smoke_deployment.py --base-url http://127.0.0.1:3000 --all-scenarios
```

The smoke command requires fresh ticket/batch tables and the original Chetan read permission.
It creates a real demo ticket, approves write access, confirms separate readback and automatic
evaluation, and runs all 37 isolated scenarios. It checks readiness before any write and requires
`demo`/`local_hash`, enabled demo identities, and an enabled worker. It never resets permissions
or blindly retries a write. On failure, its JSON output includes IDs already created for inspection.
Use `--scenario-id ID` repeatedly for a subset, or omit selection flags for four representative
scenarios. `--timeout-seconds` controls the overall deadline.

After reviewing acceptance results, this command removes **only the named acceptance project's**
containers and database volume. It is destructive to that project's stored data:

```powershell
docker compose -p resolveai-acceptance down --volumes --remove-orphans
```

## Native development

Use Python 3.12 and Node.js 22.13 or newer. Start a local PostgreSQL instance with pgvector
(for example, `docker compose up -d postgres`), then from the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e backend
Copy-Item .env.example .env
Set-Location backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m app.db.seed
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

If `.env` already exists, preserve it and review its database settings instead of copying over it.
On macOS/Linux create the environment with `python3.12 -m venv .venv`, activate it with
`source .venv/bin/activate`, and use `python` for the same pip, migration, seed and Uvicorn commands.
Copy the safe template with `cp .env.example .env` only when the local file does not exist.

In a second terminal, from `frontend/`, run `npm ci` and `npm run dev`.
The server-side proxy defaults to `http://127.0.0.1:8000`. Set `BACKEND_URL` in a local
environment file if needed. For a production frontend build, use `npm run build` then `npm start`.
See [frontend setup](../frontend/README.md) for details. Keep the application bound to localhost.

## Configuration and image layout

### Windows prerequisites

Docker Desktop's WSL 2 backend requires WSL and the Windows Virtual Machine Platform feature.
Windows feature installation needs an Administrator PowerShell window and may require a restart;
Docker Desktop itself supports a per-user installation. Follow the current
[Docker Windows setup instructions](https://docs.docker.com/desktop/setup/install/windows-install/).

For a minimal setup, `wsl --install --no-distribution` avoids installing an unnecessary Linux
distribution. To explicitly prevent an automatic restart during prerequisite installation, use
Microsoft's documented [offline WSL installation](https://learn.microsoft.com/en-us/windows/wsl/install#offline-install)
with the official WSL MSI, run it with `/quiet /norestart`, and enable
`VirtualMachinePlatform` with DISM's `/All /NoRestart` options. Complete any required Windows
restart, open Docker Desktop, review its first-launch terms, and wait for its Linux engine.
`docker info` must succeed before running the Compose commands above.

### Application settings

Backend configuration is environment based. Compose supplies `DATABASE_URL` from `POSTGRES_*`
and forwards provider, retrieval, session, and worker settings. Values containing reserved URL
characters need proper URL encoding. Do not print rendered Compose environment values when they
contain credentials; `config --quiet` validates without printing them.

The backend image uses Python 3.12, locked runtime dependencies, application code, Alembic
migrations, and policy sources. It runs as an unprivileged user and excludes development tests,
reports, caches, and local environment files. PostgreSQL stores domain and checkpoint state;
scenario fixtures use disposable OS temporary directories. The frontend image uses the Next.js
standalone build with copied static/public assets and runs as the Node user. Its server-only
`BACKEND_URL` points to `http://backend:8000`; credentials never belong in browser build variables.

Checkpoint serialization explicitly permits the application's Permission enum, keeps pickle
disabled, and rejects other unregistered constructors. Containers and CI enable strict msgpack
mode. Do not add broad serialization allowlists to silence a failed checkpoint check.

## Migrations and upgrades

Apply migrations before starting a newer backend:

```powershell
docker compose run --rm migrate
docker compose run --rm seed
```

Startup already performs those steps. Seeding preserves changed permissions. Reindexing or changed
policy content never silently relaxes the committed authorization manifest. Provider/model changes
require policy re-embedding. Keep the existing database volume during upgrades and back it up first.

Evaluation batches record their evaluator version. Upgrading the scorer preserves historical
batch results and creates a new live evaluation version through idle backfill. An unfinished batch
from an incompatible evaluator/catalog version fails safely and releases its slot; submit a new
batch with the current release. Evaluation failure never changes a resolved ticket back to failure.

## Health, diagnostics, and recovery

```powershell
docker compose ps -a
docker compose logs --tail 100 backend
docker compose logs --tail 100 seed migrate
docker compose logs --tail 100 frontend
```

`/api/health` is process liveness. `/api/ready` checks database connectivity and Alembic revision,
and reports non-secret provider/worker/demo-session settings. It does not certify that a policy
index is usable or that a provider account has quota. The deployment smoke checks actual behavior.

The worker persists queue claims, step deltas, tool idempotency keys, approvals, and graph
checkpoints. A normal shutdown stops new claims. A long-running operation interrupted by process
exit can recover after its lease expires (180 seconds by default). Permission effects and audit
results commit together, so recovery checks reuse committed tools rather than repeating effects.
An approval pause is durable and has no active provider request. Human decisions queue the same run.
Run only one backend process in v0.1; do not add Uvicorn worker processes to increase throughput.

If a scenario batch stops progressing, inspect its status and backend logs. One case is leased
at a time; transient failures retry after lease expiry, with three attempts. A failed batch retains
completed case results. When the worker is disabled, ticket submissions and batch submissions
remain queued; the UI must not be interpreted as completion.

## Automated deployment gate

`.github/workflows/deployment.yml` builds and launches the complete Compose project on a disposable
Linux runner, checks the frontend URL, runs the HTTP acceptance test through the Next.js proxy,
restarts backend/frontend, and verifies persisted ticket/evaluation history. Failure artifacts
contain container status and logs. Cleanup removes only the dedicated CI project and its volume.
Backend and frontend workflows separately run unit/integration and browser checks.

See [current release validation](release-validation.md) for fresh measured results. Use a dedicated acceptance project and separate ports to preserve existing demo data.
