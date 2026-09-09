# REST contracts

All routes use `/api`. IDs are UUID strings except human-readable employee IDs (`EMP001`).
Dates are UTC timestamps. Paginated routes return `{items, total, limit, offset}` with default
`limit=20`, maximum `100`, and nonnegative `offset`. Unknown JSON fields are rejected.

## Tickets, catalog and health

| Method / route | Request | Success |
|---|---|---|
| POST `/tickets` | `{employee_id, request_text}` | `201` Ticket + Location header |
| GET `/tickets` | Optional `status`, `employee_id`, `limit`, `offset` | `200` ticket page |
| GET `/tickets/{id}` | — | `200` Ticket + `messages[]` |
| PATCH `/tickets/{id}` | `{request_text}` | `200` updated Ticket |
| DELETE `/tickets/{id}` | — | `204`, unprocessed OPEN only |
| GET `/employees` | `limit`, `offset` | `200` employee page |
| GET `/repositories` | `limit`, `offset` | `200` repository page |
| GET `/health` | — | `200` `{status:"ok",version:"0.1.0"}` |
| GET `/ready` | Database/migration readiness plus non-secret runtime settings | `200` `{status:"ready",database:"connected",schema_revision,llm_provider,embedding_provider,demo_auth_enabled,agent_worker_enabled}` |

Ticket:

```json
{
  "id": "f07636d7-1ec0-424e-bbd8-d2fd561d6d76",
  "employee_id": "EMP001",
  "request_text": "I need write access to the payments repository.",
  "status": "OPEN",
  "created_at": "2026-09-05T10:00:00Z",
  "updated_at": "2026-09-05T10:00:00Z",
  "resolved_at": null,
  "final_response": null
}
```

Ticket detail messages contain `id`, `role` (`user`, `assistant`, `system`), `content`,
and `created_at`. Initial request text is trimmed, must be 3–8000 characters, and is persisted
atomically with a user message. PATCH also updates that initial message. Once processing has
started, requests and their audit history cannot be edited/deleted through ticket CRUD.
Status is never accepted from ticket-create or update payloads. Creating a ticket leaves it
`OPEN`; the separate run submission endpoint starts processing.

Errors use `{ "error": { "code": "ticket_not_found", "message": "Ticket does not exist." } }`.
Validation errors add `details` with `location`, `type`, and `message`; submitted input is not
echoed. Unknown entities return `404`, inactive employees or immutable tickets `409`, malformed
inputs `422`, and unavailable database/schema `503`. Responses carry an `X-Request-ID`.
Database SQL and credentials are not exposed to clients or structured application logs.
`/health` is liveness; `/ready` requires a reachable database at the current migration revision.

Interactive [OpenAPI UI](http://localhost:8000/docs) and [OpenAPI JSON](http://localhost:8000/openapi.json)
reflect the implemented schema.

## Workflow submission and execution traces

| Method / route | Request / behavior | Success |
|---|---|---|
| POST `/tickets/{id}/run` | No body; submit an OPEN ticket or reuse its active run | `202` `{run_id,ticket_id,status}` + Location header |
| GET `/tickets/{id}/runs` | `limit`, `offset`; newest queued run first | `200` run page |
| GET `/agent-runs/{id}` | Validated public state and execution metadata | `200` run |
| GET `/agent-runs/{id}/steps` | `limit`, `offset`; sequence order | `200` step page |
| GET `/agent-runs/{id}/tools` | `limit`, `offset`; execution time order | `200` tool page |

Submission atomically commits a `PENDING` run and changes its ticket to `PROCESSING` before
responding. The worker may begin immediately, so clients poll the run endpoint for progress.
Repeated submissions reuse a `PENDING`, `RUNNING`, or `WAITING_FOR_APPROVAL` run; submitting a
terminal ticket returns `409 ticket_not_open`. Missing run/ticket returns `404`; configuring
OpenAI without a key returns `503 provider_not_configured` without creating a pending run.

Example accepted response:

```json
{
  "run_id": "b8a3d668-1cde-4f56-8bf8-0b209fd06da1",
  "ticket_id": "f07636d7-1ec0-424e-bbd8-d2fd561d6d76",
  "status": "PENDING"
}
```

Run detail fields are `id`, `ticket_id`, `status`, `state`, `provider_name`, `model_name`,
`queued_at`, `started_at`, `completed_at`, `attempt_count`, and `error`. `state` is the
validated AgentState schema: identity/resource snapshots, intent, requested/current permission,
public decision summary, retrieved policy evidence, approval/execution/verification records,
current node and outcome. `workflow_version` distinguishes the original lookup workflow (`1`),
the policy-grounded recommendation workflow (`2`), and the approval/execution workflow (`3`). `retrieval_config` stores provider, model, dimensions, top-k and
minimum score for consistent recovery.
Lease ownership and credentials are not exposed by this response.

Steps contain `id`, `sequence`, `node`, `status`, `summary`, `details`, `started_at`,
`completed_at`, and `latency_ms`. `details.state_delta` records the validated public update
for that node and supports replay. Sequence numbers may have gaps when conditional routing
skips nodes. Tools contain `id`, `step_id`, `tool_name`, `arguments`, `result`, `status`,
`error`, `started_at`, `completed_at`, and `latency_ms`. Error fields use safe codes/messages.
No private chain-of-thought or raw provider response is included.

The payments request first reaches `WAITING_FOR_APPROVAL` with nine steps and six tool calls.
After approval it completes with 14 steps, ten tools, ticket `RESOLVED`, and outcome `granted`.
The initial access snapshot remains `current_permission=read`; verification records the new
`observed_permission=write`. Existing sufficient access resolves with outcome `already_sufficient`.
Rejection produces `ESCALATED`/`rejected`, while unsafe or unsupported requests escalate.
Provider, retrieval, or tool failures can produce run `FAILED` and ticket `ESCALATED`;
exhausted infrastructure recovery marks the ticket `FAILED`.

## Policy retrieval

| Method / route | Request / behavior | Success |
|---|---|---|
| GET `/policies` | `limit`, `offset`; slug order | `200` policy source page |
| GET `/policies/{id}` | UUID document ID | `200` policy source including Markdown content |
| POST `/policies/search` | `{query,top_k?,document_slugs?}` | `200` retrieval result |

Source records include `id`, `slug`, `title`, `version`, `content_hash`, and `updated_at`.
Detail adds `content`. Search accepts a trimmed query of 3–2000 characters, top-k of 1–20
(default 8), and up to eight optional document slug filters. It returns `evidence[]`,
`embedding_provider`, `embedding_model`, and `dimensions`. Each evidence item includes
`document_id`, `chunk_id`, `slug`, `title`, `version`, `section`, `excerpt`, `score` (cosine
similarity), and `content_hash` (the source document fingerprint).

A valid search with no relevant results returns `200` with empty evidence. Missing/inconsistent
index, invalid embeddings or unavailable embedding provider return `503` with a safe error code.
Unknown source IDs return `404`; invalid contracts return `422`. No source content can be
modified through these endpoints. The search route is a diagnostic read; agent searches run
through `search_policies` and are persisted as ToolExecution records. `/ready` checks database
connectivity and migrations; it does not certify policy index readiness.

## Demo sessions and human approvals

Protected endpoints require `Authorization: Bearer <access_token>`. OpenAPI exposes the
`DemoBearerSession` security scheme. Demo tokens are HMAC-signed, expire after the configured
lifetime, and reload user role and employee eligibility from the database on every request.
They are local demo sessions, not JWT/OIDC or enterprise authentication.

| Method / route | Request / behavior | Success |
|---|---|---|
| GET `/demo/users` | Active demo identities; selector must be enabled | `200` user array |
| POST `/demo/session` | `{user_id}`; selector must be enabled | `200` `{access_token,token_type,expires_at,user}` |
| GET `/session` | Valid bearer token | `200` current user identity |
| GET `/approvals` | Bearer token; optional `status`, `limit`, `offset`; actor-visible records | `200` approval page |
| GET `/approvals/{id}` | Bearer token; requester, designated manager, or admin | `200` approval detail |
| POST `/approvals/{id}/approve` | Bearer token; `{comment?}` | `200` recorded approval; resume queued atomically |
| POST `/approvals/{id}/reject` | Bearer token; `{comment?}` | `200` recorded rejection; resume queued atomically |

Demo selection returns `404` when disabled and is prohibited under production configuration.
Clients cannot supply roles. Missing/invalid/expired sessions return `401`; unauthorized review
or self-approval returns `403`. Unknown records return `404`. Reviews before the durable pause,
scope conflicts or opposite repeat decisions return `409`. Comments are optional, trimmed,
and limited to 2000 characters. Same-decision retries preserve the original actor/time/comment
and do not enqueue another run. Approval means the human decision was saved; execution remains
asynchronous and must be observed through the run endpoint.

Approval detail includes all persisted ApprovalInfo fields plus `employee`, `repository`,
`approver`, and `can_decide`. The scope contains run/ticket/employee/repository IDs, permission,
policy evidence, recommendation and assigned reviewer. Decision audit contains status,
`requested_at`, `decided_at`, `decided_by_id`, and `decision_comment`. Only the assigned current
manager or an active administrator can decide, and the requester can never self-approve.
Requesters can inspect their approvals without receiving permission to decide them.

## Evaluation batches and results

| Method / route | Request / behavior | Success |
|---|---|---|
| GET `/evaluations/scenarios` | Versioned, reviewable expected properties; excludes internal fault controls | `200` scenario array |
| POST `/evaluations/run` | Manager/admin bearer token; `{scenario_ids?: string[], submission_key?: UUID}` | `202` batch plus `Location` header |
| GET `/evaluations/batches` | `limit`, `offset`; newest first | `200` batch page |
| GET `/evaluations/batches/{id}` | UUID | `200` batch and durable progress |
| GET `/evaluations/summary` | `source=live` or `scenario`; optional `batch_id` | `200` metrics and sample counts |
| GET `/evaluations/results` | `source`, optional `batch_id`, `run_id`, `limit`, `offset` | `200` result page with assertions |
| GET `/evaluations/results/{id}` | UUID | `200` result, complete saved trace, and optional scenario expectations |

Omitting scenario IDs selects the complete catalog. Unknown/duplicate IDs return `422`; an
empty list is invalid. Submitters should retain a UUID submission key across uncertain retries.
The same key, actor, and scenario set return the original batch; a changed actor/set returns
`409`. A separate active batch also returns `409`. Missing identity returns `401`; employees
cannot submit (`403`). Read routes follow the existing local demo trace visibility model.

Batch fields include ID, requesting user, status (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`),
catalog/evaluator versions, selected scenario IDs, total/completed counts, timestamps, and safe error.
The worker runs one isolated scenario per idle iteration. `COMPLETED` means every selected case
has a result; inspect `passed` and assertions to determine whether behavior met expectations.
A failed batch may retain partial results. Internal leases are not exposed.

Results include `source`, optional live `run_id` or scenario `batch_id`, scenario/evaluator version,
metrics, named assertions, `passed`, active latency, human wait, and observed ticket context.
Detail includes the full trace used to score that result and scenario expectations. Isolated
ticket/run IDs inside a scenario snapshot do not refer to application ticket endpoints.

Live filters select all live results, optionally one run. Scenario filters default to the latest
batch and accept an explicit historical batch ID. Live plus batch ID is invalid (`422`); unknown
IDs return `404`. Aggregates include result/pass counts, catalog/selection counts, per-metric
sample counts, and average active/human-wait latency. No samples return `null`, never fabricated
zero or perfect scores. Live escalation correctness is `null` without a predefined expected
outcome. API ratios use 0–1; UI percentages multiply by 100. Only the current evaluator version
appears in live aggregate/list routes. Scenario routes honor their selected batch's recorded
evaluator version, including historical batches; saved result detail remains accessible by ID.

The new-ticket UI creates a ticket and submits its run immediately using the existing
two endpoints. If submission fails after creation, the UI retains the ticket and retries its
run without creating a second ticket. No new backend contracts were required for the UI.

Approval records include employee and repository display data, permission, relevant policy
snapshots, recommendation, designated approver, status, and permanent decision actor/time/comment.
Same-decision retries are idempotent; a conflicting second decision returns `409`.
Unauthorized actors get `403`; absent identity gets `401`. Approval does not immediately imply
success: the resumed run must execute the tool and verify access before resolving.

Agent steps expose `sequence`, `node`, `status`, `summary`, `started_at`, `completed_at`,
`latency_ms`, and structured details. Tool execution records expose tool name, validated input,
redacted result, status, and timing; no private chain-of-thought is present.
