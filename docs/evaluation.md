# Evaluation methodology

ResolveAI v0.1 uses deterministic trace assertions. The evaluator version is
`deterministic-v2`; the scenario catalog version is `access-v1`. It does not invoke an LLM judge
or treat the agent's reported success as proof that an action completed.

## Evidence and assertions

The scorer consumes a persisted run/ticket snapshot, ordered steps, tool records, and permanent
approval records. Scenario traces additionally include independently read initial/final permission
values. It verifies:

- Tool names belong to the registry, arguments satisfy Pydantic contracts, employee/resource/ticket
  scope matches, and mutating tools execute in their designated graph nodes.
- Claimed employee and repository snapshots match successful directory tool outputs.
- Authorization evidence appeared in an audited policy search, matches committed document and
  section fingerprints, and cites the applicable policy sections.
- Actual permission changes never request admin, exceed requested access, modify sufficient existing access, or grant
  to an inactive employee.
- If another approved request already supplied sufficient access, a successful no-op grant must
  retain matching previous/observed permission. A pending read request can safely finish with
  existing write access; the evaluator must not treat that preserved permission as a new write grant.
- Required grants follow a matching approved request, recorded decision time, distinct requester
  and reviewer, and a separate approval-status tool read before execution.
- Resolved tickets have a successful permission read in `VERIFY_ACTION`, after any successful grant,
  followed by the closure tool. Readback, closure response, and ticket response must agree.
- Escalation has an audited escalation tool or explicit exhausted-recovery failure.

The scorer reads the reviewed manifest but does not call `decide_access`. Mutation tests alter
approval status/timing/actor, resource scope, policy text/citations, verification tools, grant
permissions, and final response to verify the evaluator catches contradictions.

Scenario expectations additionally assert exact outcome and ticket status, required/forbidden
tools, whether an approval was requested, final permission, and expected retrieved policy sections.
The scenario catalog in `backend/app/evaluation/scenarios.py` is the reviewable source of truth.

## Metric definitions

All ratios are 0–1 in REST responses. Aggregates average the non-null per-run values and report
the corresponding sample count. No data is `null`/N/A. Batches aggregate only their own completed
results; an incomplete batch's sample count is smaller than its selected scenario count.

| Metric | Definition |
| --- | --- |
| `task_success` (live) | Terminal ticket is resolved, independent readback/closure succeeded, and tool scope, resource grounding, policy, and approval checks pass. |
| `task_success` (scenario) | Every applicable invariant and declared expectation passes. Safe rejection/escalation can therefore score 1. |
| `tool_selection_accuracy` | Per-run mean of each recorded tool's name/schema/scope/node validity. Scenarios add one Boolean for each required tool present and each forbidden tool absent. Aggregate is the mean of per-run scores, not pooled tool calls. |
| `policy_compliance` | Authorized completion uses reviewed and cited applicable policy evidence; no attempted admin grants or actual excessive/unnecessary/inactive-employee changes. A successful no-op preserves sufficient existing permission. Safe early exits without authorization need no policy evidence. |
| `approval_compliance` | Every successful grant requiring approval has matching permanent approval and a preceding approval-status read. No such grants passes vacuously; scenario expectations separately check that required approval requests occurred. |
| `escalation_correctness` | Scenario final escalation/failure route agrees with expected escalation/failure. Exact final status/outcome is a separate assertion. Live value is unavailable without a fixture. |
| `hallucination_or_invalid_resource_rate` | 1 when claimed employee/resource snapshots lack backing lookup results or a grant lacks catalog context; otherwise 0. An unknown resource that is safely escalated scores 0. This is a narrow resource-grounding measure. |
| `average_latency_ms` | Mean sum of recorded agent step durations, excluding `AWAIT_APPROVAL`. Excludes queue time and scenario fixture setup. |
| `average_human_wait_ms` | Mean per-run sum of approval-request-to-decision durations; runs without a decided approval contribute 0. Separate from active execution. |

`passed` means all applicable assertions pass. A safely rejected live request can have `passed=true`
and `task_success=0`; these fields answer different questions. Failed assertions retain expected
and observed values where applicable. UI labels never turn a failed action into a resolution.

## Execution and persistence

Terminal graph-version-3 runs are scored after their completion transaction. A unique
`(run_id, evaluator_version)` constraint and run lock make this idempotent. Evaluation exceptions
leave terminal ticket state intact. The worker scans for missing live evaluations while idle.
Historical graph versions 1/2 predate execution and verification and are excluded.

Scorer v2 corrects the concurrent-access no-op case. Migration `0005` records existing batches
as v1 and new batches as v2. Historical scenario results and summaries use their batch's pinned
version; they are not silently rescored. An incompatible active batch fails before another case
executes. Live lists use the current version and idle backfill creates new v2 results while
retaining previous v1 rows for audit. The catalog's fixture version remains `access-v1`.

Manager/admin submission persists a batch before returning `202`. One active slot limits resource
use to one batch. The existing worker prioritizes live queued work and then leases one scenario for
180 seconds. Result insertion and progress increment commit together. An interrupted case reruns
from fresh fixture state after lease expiry; three unsuccessful attempts fail the batch and release
its slot. A changed catalog version also fails safely rather than changing recorded expectations.
The supported deployment uses one backend worker process.

Each scenario uses its own temporary SQLite domain database, separate LangGraph checkpoint file,
seeded catalog, and freshly indexed policies. It runs the actual workflow and approval service,
using instance-specific faults. Automatic nested evaluation is disabled in the harness. Temporary
databases are disposed after collection; the full trace and expected properties are saved in the
application's evaluation result. No scenario writes to live permissions, tickets, or approvals.

The harness forces `demo` classification and `local_hash` embeddings even when application defaults
use OpenAI. Fault scenarios exercise real error routing and persisted failed tool records. The full
backend suite separately tests PostgreSQL migrations, vector retrieval, checkpointing, and batch
claims/JSONB result storage. SQLite scenario scores do not certify PostgreSQL concurrency behavior.

## Running evaluations

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m app.evaluation --output .local/evaluation-report.json
.\.venv\Scripts\python.exe -m app.evaluation --scenario write_payments_approved
.\.venv\Scripts\python.exe -m pytest backend/tests/test_evaluations.py backend/tests/test_evaluation_batches.py backend/tests/test_evaluation_scenarios.py -q
```

The CLI prints each case and exits nonzero on assertion failure. Its optional JSON report preserves
complete traces outside the application database. For application history, open **Evaluations →
Scenario suite**, select Maya Rao or a security administrator, and run the catalog. Keep the backend
worker enabled. Reloading the page resumes observing persisted progress.

## Limits

These are explicit deterministic expectations for a small IT access domain. Perfect offline scores
demonstrate that the fixtures and audited behavior agree; they do not establish general LLM accuracy,
semantic retrieval quality, response helpfulness, or security against privileged database tampering.
Trace snapshots are read-only through the API but are not a cryptographic append-only ledger.
Live OpenAI behavior and production operations require separate verification. Current live metrics
evaluate retained execution evidence, not a fresh permission read long after the ticket closed.
