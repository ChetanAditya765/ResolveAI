# ResolveAI v0.1 demo walkthrough

This five-minute tour demonstrates a persisted access request, policy retrieval, manager approval,
tool execution, independent verification, and automatic evaluation in the running product.

For recording setup, narration and a shot-by-shot sequence, see the
[demo recording guide](recording.md).

## Start the workspace

From the repository root, with Docker running:

```sh
docker compose up --build --wait --wait-timeout 180
```

Open [ResolveAI](http://localhost:3000). The default configuration needs no API key. If you have
customized provider settings, select `LLM_PROVIDER=demo` and `EMBEDDING_PROVIDER=local_hash`
before startup and policy seeding. Use the [deployment runbook](deployment.md) for diagnostics.

In this mode, request classification uses a narrow deterministic parser and embeddings use a
lexical baseline. The LangGraph workflow, PostgreSQL/pgvector retrieval, approval checks, tools,
checkpoint persistence, and evaluation all execute. Repository changes are simulated. The OpenAI
adapter is implemented, but paid provider calls are not part of the verified offline demo.

## Five-minute product tour

The primary sequence assumes freshly seeded permissions: Chetan has read access to `payments`.
After a successful grant, use the repeat-demo instructions below or inspect the completed ticket.

| Time | Action                                                                                                           | Evidence to show                                                                                                                                   |
| ---- | ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0:00 | Open the dashboard.                                                                                              | Ticket status counts, recent activity, and the repository catalog. Explain that the agent acts within an explicit access workflow.                 |
| 0:30 | Open **New ticket**, choose **Chetan Aditya**, and submit `I need write access to the payments repository.`      | The employee and repository are identified, current access is checked, and policy evidence is retrieved.                                           |
| 1:15 | Open the ticket once its status is `WAITING_FOR_APPROVAL`.                                                       | Repository Access Policy §3 requires manager approval. The timeline pauses before any permission grant. Expand the policy and tool records.        |
| 2:00 | In **Demo identity**, select **Maya Rao**. Open **Approvals**, review this ticket, and click **Approve access**. | The request shows the employee, repository, requested permission, policy, and agent recommendation. The human decision is saved permanently.       |
| 2:45 | Return to the ticket and wait for `RESOLVED`.                                                                    | The same run resumes. The grant tool succeeds, a separate permission read verifies write access, and the final response cites the policy.          |
| 3:45 | Inspect **Run evaluation**, then open **Evaluations**.                                                           | The primary run passes eight trace assertions. Compare live results with the isolated scenario suite, including safe failure and escalation cases. |

For the standard successful approval path, expect **14 timeline steps**, **10 tool executions**,
one permission grant, and a successful independent verification. Each step shows its timestamp,
status, description, and latency. Decision summaries explain the applicable rule without storing
private chain-of-thought.

The top-bar **Demo identity** controls reviewer and evaluation permissions. The **Employee** field
on New ticket independently chooses the requester. This local identity selector is a demo feature.

## Show evaluation evidence

Select Maya or a security administrator and submit the scenario suite from **Evaluations**.
The 37 cases run in isolated databases and cannot change the demo employees' actual permissions.
Wait for the batch to complete; pre-run it before a timed presentation if necessary.

- **Live runs:** success means a verified resolution. An appropriate escalation is not counted
  as a resolved task; escalation correctness needs an expected outcome and is unavailable here.
- **Scenario suite:** success means the observed outcome matches the fixture, including expected
  approval rejection, unknown resources, and tool or verification failures.
- **Evidence:** inspect an individual result's assertions and saved trace. N/A means an assertion
  or metric does not apply, rather than a failed check.

The catalog contains **37 deterministic scenarios**; see [current results](release-validation.md). This measures a fixed suite;
it is not an estimate of live-model accuracy. Failure injection is provided by scenario fixtures,
not by special words typed into a ticket. See [evaluation methodology](evaluation.md).

## Additional product cases

These examples assume the listed employee/repository permissions have not already changed.
Choose the requester in the New ticket form; choose a reviewer separately when approval is needed.

| Requester                                 | Request                                                    | Expected behavior                                                                                            |
| ----------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Rahul Iyer                                | `I need read access to the payments repository.`           | Same-department read access is granted and verified automatically.                                           |
| Chetan Aditya                             | `I need write access to the platform-api repository.`      | Maya's approval is required. Reject to demonstrate escalation without changing the existing read permission. |
| Nisha Mehta                               | `I need write access to the finance-reporting repository.` | Finance manager Kabir Shah can approve; then the agent grants and verifies write access.                     |
| Chetan Aditya                             | `I need admin access to the payments repository.`          | Escalates for security review; never grants admin automatically.                                             |
| Chetan Aditya                             | `I need write access to the phantom-ledger repository.`    | Escalates because the repository does not exist; no invented resource or permission change.                  |
| Chetan Aditya, after the primary approval | `I need write access to the payments repository.`          | Verifies existing sufficient access and resolves without another grant or approval.                          |

Rahul is present in the Employee dropdown but has no top-bar demo session. This does not prevent
submitting a request for him in the local demo.

## Repeat the demo without erasing history

Permissions survive restarts and seeding. Once Chetan receives write access to `payments`, the
same request should skip approval because the permission is already sufficient. Do not reset
permissions or delete the database to make the agent appear to repeat a change.

To show the exact first-time approval again, start a separate Compose project with its own volume.
Only one project can use the default ports at a time. Choose an unused project name; reusing a
name restores that project's earlier database.

Windows PowerShell, from the repository root:

```powershell
$env:LLM_PROVIDER = "demo"
$env:EMBEDDING_PROVIDER = "local_hash"
$env:DEMO_AUTH_ENABLED = "true"
$env:AGENT_WORKER_ENABLED = "true"
$env:APP_ENV = "development"
docker compose -p resolveai stop
docker compose -p resolveai-demo-02 up --build --wait --wait-timeout 180
```

macOS/Linux:

```sh
export LLM_PROVIDER=demo EMBEDDING_PROVIDER=local_hash
export DEMO_AUTH_ENABLED=true AGENT_WORKER_ENABLED=true APP_ENV=development
docker compose -p resolveai stop
docker compose -p resolveai-demo-02 up --build --wait --wait-timeout 180
```

Return to the original workspace afterward:

```sh
docker compose -p resolveai-demo-02 stop
docker compose -p resolveai up --wait --wait-timeout 180
```

Both histories remain stored. If you use a custom Compose project name for your regular workspace,
substitute it for `resolveai` in these commands. Provider settings above apply to the current shell.

## Present the release accurately

v0.1 is a working local portfolio application with simulated enterprise tools. It includes durable
human approval, real vector retrieval, tested policy safeguards, and deterministic evaluations.
Public hosting still needs broader route authorization, production identity, TLS, and operational
backups. GitHub Actions workflows are supplied but have not been run remotely from this workspace.
See the [release notes](../CHANGELOG.md) and [verification record](release-validation.md).
