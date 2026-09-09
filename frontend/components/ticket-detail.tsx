"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  Clock3,
  Code2,
  GitBranch,
  Play,
  ShieldCheck,
  X,
} from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { dateTime, humanize, latency, shortId } from "@/lib/format";
import { usePoll } from "@/lib/use-poll";
import type {
  AgentRun,
  AgentStep,
  Approval,
  Page,
  TicketDetail as TicketData,
  ToolExecution,
} from "@/types/api";
import { ApprovalCard } from "./approval-card";
import { LiveRunEvaluation } from "./live-run-evaluation";
import { PolicyEvidence } from "./policy-evidence";
import { useSession } from "./session-provider";
import { EmptyState, ErrorNotice, Loading, StatusBadge } from "./ui";

const nodeLabels: Record<string, string> = {
  RECEIVE_REQUEST: "Request received",
  IDENTIFY_EMPLOYEE: "Employee identified",
  CLASSIFY_REQUEST: "Request classified",
  IDENTIFY_RESOURCE: "Repository identified",
  CHECK_CURRENT_ACCESS: "Current access checked",
  RETRIEVE_POLICY: "Policy retrieved",
  PLAN_ACTION: "Action planned",
  REQUEST_APPROVAL: "Manager approval requested",
  AWAIT_APPROVAL: "Human decision boundary",
  CHECK_APPROVAL: "Approval decision checked",
  EXECUTE_ACTION: "Permission tool executed",
  VERIFY_ACTION: "Permission verified",
  RESPOND: "Response prepared",
  RESOLVE: "Ticket resolved",
  ESCALATE: "Ticket escalated",
};
export function TicketDetail({ id }: { id: string }) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  const [tab, setTab] = useState("timeline");
  const { session } = useSession();
  const { data, error, refresh } = usePoll(
    useCallback(
      async (signal) => {
        const [ticket, runs] = await Promise.all([
          api<TicketData>(`/tickets/${id}`, { signal }),
          api<Page<AgentRun>>(`/tickets/${id}/runs?limit=1`, { signal }),
        ]);
        const run = runs.items[0];
        if (!run)
          return {
            ticket,
            run,
            steps: [] as AgentStep[],
            tools: [] as ToolExecution[],
          };
        const [steps, tools] = await Promise.all([
          api<Page<AgentStep>>(`/agent-runs/${run.id}/steps?limit=100`, {
            signal,
          }),
          api<Page<ToolExecution>>(`/agent-runs/${run.id}/tools?limit=100`, {
            signal,
          }),
        ]);
        return { ticket, run, steps: steps.items, tools: tools.items };
      },
      [id],
    ),
    2000,
  );
  async function start() {
    setBusy(true);
    setFailure(undefined);
    try {
      await api(`/tickets/${id}/run`, { method: "POST" });
      refresh();
    } catch (failure) {
      setFailure(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  if (!data)
    return error ? (
      <ErrorNotice message={error} retry={refresh} />
    ) : (
      <Loading label="Loading ticket and agent trace…" />
    );
  const { ticket, run, steps, tools } = data;
  const state = run?.state;
  return (
    <>
      <Link href="/" className="back-link">
        <ArrowLeft size={15} />
        All requests
      </Link>
      <div className="ticket-heading">
        <div>
          <div className="eyebrow">
            ACCESS REQUEST <span className="mono">RA-{shortId(id)}</span>
          </div>
          <h1>
            {state?.repository?.name
              ? `${humanize(state.requested_permission ?? "Repository")} access · ${state.repository.name}`
              : "Repository access request"}
          </h1>
          <p>
            Created {dateTime(ticket.created_at)} ·{" "}
            {state?.employee?.name ?? ticket.employee_id}
          </p>
        </div>
        <StatusBadge status={ticket.status} />
      </div>
      {error && (
        <ErrorNotice
          message={`Live refresh failed. Showing the last loaded trace. ${error}`}
          retry={refresh}
        />
      )}
      {failure && <ErrorNotice message={failure} />}
      <section className="panel request-original">
        <span className="quote-mark">“</span>
        <div>
          <div className="eyebrow">ORIGINAL REQUEST</div>
          <p>{ticket.request_text}</p>
        </div>
        {ticket.status === "OPEN" && (
          <button
            className="button primary"
            onClick={() => void start()}
            disabled={busy}
          >
            <Play size={15} />
            {busy ? "Starting…" : "Start agent run"}
          </button>
        )}
      </section>
      {ticket.final_response && (
        <section
          className={`outcome-panel ${ticket.status === "RESOLVED" ? "resolved" : "escalated"}`}
          data-testid="final-response"
        >
          <span>
            {ticket.status === "RESOLVED" ? (
              <CheckCircle2 size={24} />
            ) : (
              <ShieldCheck size={24} />
            )}
          </span>
          <div>
            <div className="eyebrow">
              {ticket.status === "RESOLVED"
                ? "VERIFIED RESOLUTION"
                : "FINAL RESPONSE"}
            </div>
            <p>{ticket.final_response}</p>
          </div>
        </section>
      )}
      <div className="detail-grid">
        <section className="panel trace-panel">
          <div className="panel-heading">
            <div>
              <h2>Agent run</h2>
              <p>
                {run
                  ? `${shortId(run.id)} · ${run.provider_name} / ${run.model_name}`
                  : "No run has been submitted"}
              </p>
            </div>
            {run && <StatusBadge status={run.status} />}
          </div>
          <div className="tabs trace-tabs">
            {[
              {
                id: "timeline",
                label: "Activity timeline",
                count: steps.length,
              },
              { id: "tools", label: "Tool executions", count: tools.length },
            ].map((item) => (
              <button
                key={item.id}
                className={tab === item.id ? "active" : ""}
                aria-pressed={tab === item.id}
                onClick={() => setTab(item.id)}
              >
                {item.label}
                <span className="tab-count">{item.count}</span>
              </button>
            ))}
          </div>
          {tab === "timeline" ? (
            steps.length ? (
              <ol className="timeline">
                {steps.map((step) => (
                  <TimelineStep key={step.id} step={step} />
                ))}
              </ol>
            ) : (
              <EmptyState
                title="Ready for execution"
                description="The persisted activity timeline will appear when the agent begins processing."
              />
            )
          ) : (
            <div className="tool-list">
              {tools.length ? (
                tools.map((tool) => <ToolCall key={tool.id} tool={tool} />)
              ) : (
                <EmptyState
                  title="No tools executed yet"
                  description="Tool arguments, results, and timing will appear here."
                />
              )}
            </div>
          )}
        </section>
        <aside className="context-column">
          <section className="panel facts-panel">
            <div className="panel-heading">
              <h2>Request context</h2>
              <GitBranch size={17} />
            </div>
            <dl className="fact-list">
              <div>
                <dt>Employee</dt>
                <dd>{state?.employee?.name ?? ticket.employee_id}</dd>
              </div>
              <div>
                <dt>Department</dt>
                <dd>{state?.employee?.department ?? "Awaiting lookup"}</dd>
              </div>
              <div>
                <dt>Repository</dt>
                <dd>{state?.repository?.name ?? "Awaiting lookup"}</dd>
              </div>
              <div>
                <dt>Requested permission</dt>
                <dd className="capitalize">
                  {state?.requested_permission ?? "Awaiting classification"}
                </dd>
              </div>
              <div>
                <dt>Initial access</dt>
                <dd className="capitalize">
                  {state?.current_permission ?? "Not checked"}
                </dd>
              </div>
              <div>
                <dt>Verified access</dt>
                <dd className="capitalize" data-testid="verified-permission">
                  {state?.verification_result?.sufficient
                    ? state.verification_result.observed_permission
                    : "Not confirmed"}
                </dd>
              </div>
            </dl>
          </section>
          {state?.decision && (
            <section className="decision-summary standalone">
              <ShieldCheck size={19} />
              <span className="eyebrow">POLICY DECISION</span>
              <p>{state.decision.summary}</p>
            </section>
          )}
          {run &&
            run.state.workflow_version >= 3 &&
            ["COMPLETED", "FAILED"].includes(run.status) && (
              <LiveRunEvaluation key={run.id} runId={run.id} />
            )}
          <section className="panel">
            <div className="panel-heading">
              <h2>Retrieved policy</h2>
              <span className="count-label">
                {state?.retrieved_policies.length ?? 0}
              </span>
            </div>
            <PolicyEvidence evidence={state?.retrieved_policies ?? []} />
          </section>
          {run && (
            <div className="trace-footnote">
              <Code2 size={15} />
              <p>
                Public decision summaries and structured tool results. No
                private model reasoning is stored.
              </p>
            </div>
          )}
        </aside>
      </div>
      {state?.approval_id && (
        <section className="ticket-approval">
          <h2 className="section-title">Human approval</h2>
          {session ? (
            <TicketApproval
              key={`${session.access_token}:${state.approval_id}`}
              id={state.approval_id}
              token={session.access_token}
              onDecision={refresh}
            />
          ) : (
            <div className="notice">
              Select a demo identity to inspect the approval. Its execution
              status remains visible in the timeline.
            </div>
          )}
        </section>
      )}
    </>
  );
}
function TicketApproval({
  id,
  token,
  onDecision,
}: {
  id: string;
  token: string;
  onDecision: () => void;
}) {
  const { data, error, refresh } = usePoll(
    useCallback(
      (signal) => api<Approval>(`/approvals/${id}`, { signal }, token),
      [id, token],
    ),
  );
  if (error) return <ErrorNotice message={error} retry={refresh} />;
  return data ? (
    <ApprovalCard
      approval={data}
      onDecision={() => {
        refresh();
        onDecision();
      }}
    />
  ) : (
    <Loading label="Loading approval…" />
  );
}
function TimelineStep({ step }: { step: AgentStep }) {
  const complete = step.status === "COMPLETED";
  const title =
    step.status === "WAITING"
      ? "Waiting for approval"
      : step.status === "FAILED"
        ? `${humanize(step.node)} failed`
        : step.status === "RUNNING"
          ? `${humanize(step.node)} in progress`
          : (nodeLabels[step.node] ?? humanize(step.node));
  return (
    <li className={`timeline-step ${step.status.toLowerCase()}`}>
      <span className="timeline-marker">
        {complete ? (
          <Check size={13} />
        ) : step.status === "FAILED" ? (
          <X size={13} />
        ) : (
          <Clock3 size={13} />
        )}
      </span>
      <div className="timeline-content">
        <div className="timeline-title">
          <h3>{title}</h3>
          <span>{latency(step.latency_ms)}</span>
        </div>
        <p>{step.summary}</p>
        <div className="timeline-meta">
          <time dateTime={step.started_at}>{dateTime(step.started_at)}</time>
          <span>{humanize(step.status)}</span>
        </div>
      </div>
    </li>
  );
}
function ToolCall({ tool }: { tool: ToolExecution }) {
  return (
    <details className="tool-call">
      <summary>
        <Code2 size={17} />
        <span className="tool-name">{tool.tool_name}</span>
        <StatusBadge status={tool.status} />
        <span className="tool-latency">{latency(tool.latency_ms)}</span>
      </summary>
      <div className="tool-body">
        <div className="tool-meta">
          {dateTime(tool.started_at)} · Execution {shortId(tool.id)}
        </div>
        <h3>Arguments</h3>
        <pre>{JSON.stringify(tool.arguments, null, 2)}</pre>
        <h3>Result</h3>
        <pre>{JSON.stringify(tool.result, null, 2)}</pre>
        {tool.error && <ErrorNotice message={tool.error} />}
      </div>
    </details>
  );
}
