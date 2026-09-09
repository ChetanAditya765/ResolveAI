# ResolveAI frontend

Next.js App Router, React, TypeScript, and Tailwind CSS. Displayed tickets, policies,
approvals, and execution traces come from the FastAPI backend.

## Run

Use Node.js 22.13+ and a migrated, seeded backend on port 8000. From this directory:

```sh
npm ci
npm run dev
```

Open http://localhost:3000. `BACKEND_URL` defaults to `http://127.0.0.1:8000`; configure it
in `frontend/.env.local` for development or as a server environment variable. No public
API key or browser API URL is needed. Production:

```sh
npm run build
npm start
```

The start script copies local static/public assets into the standalone output and launches
the same server artifact used in Docker. It accepts `--port` and `--hostname` arguments.

## Screens and behavior

| Route | Behavior |
|---|---|
| `/` | Live ticket counts, paginated/filterable request queue, repository directory |
| `/tickets/new` | Active employee selection, request submission, immediate agent run |
| `/tickets/[id]` | Original request, live timeline, policy evidence, tools, approval, final response |
| `/approvals` | Pending reviews and permanent approved/rejected decision history |
| `/policies` | Read-only versioned policy documents |
| `/evaluations` | Automatic live scores, isolated scenario batches, assertion details and saved traces |

The top bar selects a signed local demo identity. Chetan is selected initially; choose
Maya Rao to review his access request. Sessions stay in tab-scoped `sessionStorage` and
are revalidated after reload. Approval views clear on identity changes. This is a local
demo selector, not an enterprise identity provider.

The same-origin `/api` route forwards only allowlisted backend paths and selected headers,
including the bearer token. Its upstream origin is server-configured and redirects are
not followed. It does not add authorization; FastAPI remains the policy and role authority.
No domain state is stored in the frontend. Polling requests are cancelled on navigation,
do not overlap within a view, and surface errors with retry controls.

Ticket creation and run submission are separate API calls. If creation succeeds and run
submission fails, the form keeps the saved ticket and retries its run without creating
another ticket. OPEN ticket details also provide a Start agent run action.

## Verification

```sh
npm run lint
npm run format:check
npm run typecheck
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright uses the production standalone server on port 3011 and an isolated, seeded
SQLite backend on port 8011. It refuses to reuse existing servers. Tests use deterministic
providers without paid calls and never operate on the configured application database.
They exercise actual REST requests and domain tools; specific outage tests inject HTTP
failures. The backend helper removes its temporary database on graceful exit. A forced
process termination can leave a temporary directory.

The project `.venv` is detected automatically; set `RESOLVEAI_PYTHON` to select another
Python executable with the backend dependencies installed. Chromium and backend processes
must be permitted by the host environment. Screenshots and failure traces are generated
under ignored `test-results/` and `playwright-report/` directories.

The evaluation dashboard separates live task resolution from the 37 scenarios' expected behavior.
Managers/admins can submit batches; all displayed scores and traces come from persisted results.
ESLint 9 is pinned because the
current Next.js React lint plugin is incompatible with ESLint 10; revisit that development
dependency when the plugin supports the newer API.
