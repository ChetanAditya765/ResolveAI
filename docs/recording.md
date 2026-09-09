# Record a ResolveAI demonstration

Aim for a clear five- to six-minute product walkthrough. Show the request, the policy decision,
the human approval, the observed permission and the evaluation evidence. Use your own speaking
style; the suggested lines below describe what the implementation actually does.

## 1. Prepare the application

1. Start the application using the [demo walkthrough](demo.md#start-the-workspace) or
   [deployment guide](deployment.md). The default Docker URL is `http://localhost:3000`.
   A separate native recording workspace may use a different localhost port; use its supplied URL.
2. Open the product in a browser. The portfolio case study is an introduction; the recording
   should primarily show the running ResolveAI dashboard, tickets, approvals and evaluations.
3. Use a fresh, separate demonstration database for the first-time approval flow. Chetan Aditya
   must initially have **read**, not write, access to `payments`. Keep existing histories intact.
4. Use `LLM_PROVIDER=demo` and `EMBEDDING_PROVIDER=local_hash`. No paid provider key is needed.
   These settings exercise the workflow with deterministic interpretation and lexical embeddings.
5. Optional: select **Maya Rao** in **Demo identity**, open **Evaluations → Scenario suite**,
   click **Run all 37 scenarios**, and wait for completion before recording. These scenarios
   use isolated fixtures and do not change the main demo permissions.
6. Return to the dashboard. Choose **Chetan Aditya** in **Demo identity** for the opening shot.
   The requester is selected separately in the New ticket form.

Avoid submitting the main payments request during rehearsal if you want to record its first
approval in this database. Once approved, that permission persists. For another first-time take,
use a fresh separate workspace as described in [repeat the demo](demo.md#repeat-the-demo-without-erasing-history).

## 2. Set up screen recording on Windows 11

1. Open the product in a dedicated browser window. Maximize it and use 100% zoom; 1920 × 1080
   is a useful capture size when the display supports it.
2. Close unrelated tabs and keep notifications out of the capture. Frame the product page so
   terminal windows, private files and desktop messages are outside the recording area.
3. Open **Snipping Tool**, choose **Record**, then **New**. The Windows shortcut is
   **Windows + Shift + R**. Select the browser area you want to record.
4. Enable microphone capture if narrating live. Record a short sample, stop, and play it back
   to check voice level, text readability and whether the pointer is visible.
5. Start the real recording. Pause briefly at each evidence panel and scroll slowly.

Microsoft's [Snipping Tool instructions](https://support.microsoft.com/en-us/windows/apps/use-snipping-tool-to-capture-screenshots)
describe the recording controls. If your installed version lacks recording, update it through
Microsoft Store or use an already installed screen recorder.

## 3. Follow this recording sequence

### 0:00–0:30 — Introduce the system

Show the dashboard and repository directory.

Suggested narration: “ResolveAI turns repository-access requests into policy-grounded workflows.
It separates request interpretation from authorization, waits for human approval when required,
and verifies the resulting permission before resolving a ticket.”

Briefly establish the scope: “This demonstration uses simulated repository permissions and an
offline provider. The workflow, database, approvals and evaluation execute locally.”

### 0:30–1:00 — Submit the access request

1. Click **New ticket**.
2. In **Employee**, select **Chetan Aditya**.
3. Enter exactly: `I need write access to the payments repository.`
4. Click **Submit request** and wait for the ticket page.

Suggested narration: “The request is tied to a stored employee. The workflow resolves the
repository and checks existing access before making a decision.”

### 1:00–2:00 — Show policy and the durable pause

1. Wait for **Waiting for approval**.
2. Show the requested permission, current read access and **Repository Access Policy** evidence.
3. Scroll through the timeline and expand relevant tool evidence.
4. Reload the page once to show that the waiting request remains available.

Suggested narration: “Write access requires manager approval. The decision is based on reviewed
policy evidence and deterministic checks. The run is paused; no permission grant has happened.”

A browser reload demonstrates persisted UI state. It does not, by itself, demonstrate a backend
restart. Only narrate process-restart recovery if you actually record that separate operation.

### 2:00–2:45 — Record the human decision

1. Change **Demo identity** to **Maya Rao**, the manager.
2. Open **Approvals**.
3. Review Chetan's request for write access to `payments`, including the policy evidence.
4. Enter a short decision comment, such as `Business need confirmed for payments development.`
5. Click **Approve access** once.

Suggested narration: “An eligible reviewer records a scoped decision. Self-approval is blocked.
The saved approval makes the original run resumable; it is not a new request.”

### 2:45–3:45 — Show execution and independent verification

1. Return to the ticket through the dashboard/request queue.
2. Wait for **Ticket resolved** and confirm verified permission is **write**.
3. Open **Tool executions** and show the permission grant and the separate permission read.
4. Show the final response and completed timeline.

Suggested narration: “The action is revalidated before execution. After the simulated grant,
another tool reads permission independently. Closure requires matching verification evidence.”

The standard successful path has 14 timeline steps and 10 tool executions, including one grant
and separate permission reads before and after the effect. Show the actual records on screen;
do not describe expected counts as observed unless they match this run.

### 3:45–4:45 — Show evaluation

1. Show **Run evaluation** on the resolved ticket and expand its assertions.
2. Open **Evaluations → Scenario suite**.
3. If no batch is ready, select Maya and click **Run all 37 scenarios**; wait for completion.
4. Show the actual completed counts and inspect one scenario's assertions or stored trace.

Suggested narration: “Evaluation checks recorded behavior: policy evidence, approval before
grant, tool scope and verification before closure. The scenario suite also includes rejection,
unknown resources and injected failures. A perfect fixed-suite score is not universal safety.”

### 4:45–5:30 — Optional failure case and closing

For a short safety example, create another ticket for Chetan with:
`I need admin access to the payments repository.` Show its escalation without an automatic
admin grant. This remains meaningful even after write access has been granted.

Close with: “The current scope is deliberately bounded. Next steps are a controlled live-model
benchmark and a GitHub sandbox adapter with external-effect reconciliation.”

You can finish on the portfolio architecture diagram or project-download section if desired.

## 4. Save, review and link the recording

1. Stop the capture and save the video outside the repository, for example in your Videos folder
   as `ResolveAI-Demo.mp4`. Watch it once from beginning to end.
2. Trim empty waiting time and accidental window changes without changing the order of request,
   approval, execution and verification. If presenting selected clips, make that clear.
3. Check that text is readable, narration matches the visible state, and no private data appears.
4. Upload the final video to your chosen video host and verify its viewing permissions.
5. In the portfolio's `src/components/resolveai/config.js`, set `demoVideoUrl` to that URL.
   The hero button and demo section share this value. The README links to the same demo section.
6. Run the existing portfolio build, review the video link, and publish through the existing
   GitHub/Netlify process. The video does not autoplay.

## Keep the claims tied to the recording

- Explain that permission effects are simulated and the default provider is deterministic.
- PostgreSQL/pgvector claims apply when that is the running backend; an SQLite rehearsal does
  not demonstrate PostgreSQL vector retrieval or locking.
- Do not describe a native run as a Docker recording. Current verification scope is recorded in
  [release validation](release-validation.md).
- Keep the capture focused on the application and its observable evidence.
