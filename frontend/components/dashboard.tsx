"use client";
import Link from "next/link";
import { useCallback, useState } from "react";
import {
  ArrowDownRight,
  ArrowRight,
  CheckCircle2,
  Clock3,
  GitBranch,
  Layers,
  Plus,
  ShieldCheck,
} from "lucide-react";
import { api } from "@/lib/api";
import { dateTime, shortId } from "@/lib/format";
import { usePoll } from "@/lib/use-poll";
import type {
  Employee,
  Page,
  Repository,
  Ticket,
  TicketStatus,
} from "@/types/api";
import {
  EmptyState,
  ErrorNotice,
  Loading,
  PageHeader,
  StatusBadge,
} from "./ui";

const metrics = [
  {
    status: "OPEN",
    label: "Open tickets",
    note: "Ready for an agent run",
    icon: Layers,
  },
  {
    status: "PROCESSING",
    label: "Processing",
    note: "Agent execution in progress",
    icon: GitBranch,
  },
  {
    status: "WAITING_FOR_APPROVAL",
    label: "Waiting for approval",
    note: "Human decision required",
    icon: Clock3,
  },
  {
    status: "RESOLVED",
    label: "Resolved",
    note: "Access independently verified",
    icon: CheckCircle2,
  },
] as const;
export function Dashboard() {
  const [filter, setFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const counts = usePoll(
    useCallback(
      async (signal) =>
        Promise.all(
          metrics.map((metric) =>
            api<Page<Ticket>>(`/tickets?status=${metric.status}&limit=1`, {
              signal,
            }),
          ),
        ),
      [],
    ),
  );
  const catalog = usePoll(
    useCallback(async (signal) => {
      const [employees, repositories] = await Promise.all([
        api<Page<Employee>>("/employees?limit=100", { signal }),
        api<Page<Repository>>("/repositories?limit=100", { signal }),
      ]);
      return { employees: employees.items, repositories: repositories.items };
    }, []),
    30000,
  );
  return (
    <>
      <PageHeader
        eyebrow="OPERATIONS OVERVIEW"
        title="Access, with accountability."
        description="Manage employee requests from policy review to verified resolution."
      >
        <Link href="/tickets/new" className="button primary">
          <Plus size={17} />
          New ticket
        </Link>
      </PageHeader>
      {counts.error && (
        <ErrorNotice message={counts.error} retry={counts.refresh} />
      )}
      <section className="metrics-grid" aria-label="Ticket totals">
        {metrics.map((metric, index) => (
          <button
            key={metric.status}
            className={`metric-card ${filter === metric.status ? "selected" : ""}`}
            onClick={() => {
              setFilter(metric.status);
              setOffset(0);
            }}
          >
            <div className="metric-label">
              {metric.label}
              <metric.icon size={17} />
            </div>
            <div className="metric-value">
              {counts.data?.[index].total ?? "—"}
            </div>
            <div className="metric-note">
              <span className={`metric-dot dot-${metric.status}`} />
              {metric.note}
            </div>
          </button>
        ))}
      </section>
      <div className="dashboard-grid">
        <section className="panel ticket-panel">
          <div className="panel-heading">
            <div>
              <h2>Request queue</h2>
              <p>The latest activity across your workspace</p>
            </div>
            <select
              aria-label="Filter tickets by status"
              className="compact-select"
              value={filter}
              onChange={(event) => {
                setFilter(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All statuses</option>
              {[...metrics.map((m) => m.status), "ESCALATED", "FAILED"].map(
                (status) => (
                  <option key={status} value={status}>
                    {status.toLowerCase().replaceAll("_", " ")}
                  </option>
                ),
              )}
            </select>
          </div>
          <TicketQueue
            key={`${filter}:${offset}`}
            filter={filter as TicketStatus | ""}
            offset={offset}
            setOffset={setOffset}
            employees={catalog.data?.employees ?? []}
          />
        </section>
        <aside className="context-column">
          <section className="policy-summary">
            <div className="icon-tile">
              <ShieldCheck size={21} />
            </div>
            <div className="eyebrow">POLICY CONTROLS</div>
            <h2>
              A decision before
              <br />
              every action.
            </h2>
            <p>
              Requests are checked against company policy. Required approvals
              pause the agent before access changes.
            </p>
            <div className="control-row">
              <span>Write access</span>
              <span>Manager approval</span>
            </div>
            <div className="control-row">
              <span>Admin access</span>
              <span>Security escalation</span>
            </div>
            <Link href="/policies" className="text-link">
              Explore policy library <ArrowRight size={15} />
            </Link>
          </section>
          <section className="panel repository-panel">
            <div className="panel-heading">
              <h2>Repository directory</h2>
              <span className="count-label">
                {catalog.data?.repositories.length ?? "—"}
              </span>
            </div>
            {catalog.error ? (
              <ErrorNotice message={catalog.error} retry={catalog.refresh} />
            ) : (
              (catalog.data?.repositories.map((repo) => (
                <div className="repository-row" key={repo.id}>
                  <span className="repo-icon">
                    <GitBranch size={16} />
                  </span>
                  <div>
                    <strong>{repo.name}</strong>
                    <span>{repo.owning_department}</span>
                  </div>
                  <span className="sensitivity">{repo.sensitivity_level}</span>
                </div>
              )) ?? <Loading label="Loading repositories…" />)
            )}
          </section>
        </aside>
      </div>
      <div className="workflow-strip">
        <span className="eyebrow">THE REQUEST LIFECYCLE</span>
        {[
          "Identify",
          "Retrieve policy",
          "Decide",
          "Approve if required",
          "Execute",
          "Verify",
        ].map((step, i) => (
          <span key={step}>
            <span className="workflow-number">
              {String(i + 1).padStart(2, "0")}
            </span>
            {step}
            {i < 5 && <ArrowDownRight size={14} />}
          </span>
        ))}
      </div>
    </>
  );
}
function TicketQueue({
  filter,
  offset,
  setOffset,
  employees,
}: {
  filter: TicketStatus | "";
  offset: number;
  setOffset: (offset: number) => void;
  employees: Employee[];
}) {
  const { data, error, refresh } = usePoll(
    useCallback(
      (signal) =>
        api<Page<Ticket>>(
          `/tickets?limit=10&offset=${offset}${filter ? `&status=${filter}` : ""}`,
          { signal },
        ),
      [filter, offset],
    ),
  );
  if (error) return <ErrorNotice message={error} retry={refresh} />;
  if (!data) return <Loading label="Loading requests…" />;
  return (
    <>
      {data.items.length === 0 ? (
        <EmptyState
          title={
            filter
              ? "No requests in this status"
              : "Your request queue is clear"
          }
          description="Create an access request to see its policy decisions, tools, and approval history here."
          href="/tickets/new"
          action="Create a request"
        />
      ) : (
        <div className="table-scroll">
          <table className="tickets-table">
            <thead>
              <tr>
                <th>Request</th>
                <th>Employee</th>
                <th>Status</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((ticket) => (
                <tr key={ticket.id}>
                  <td>
                    <Link
                      href={`/tickets/${ticket.id}`}
                      className="ticket-link"
                    >
                      <span className="ticket-reference">
                        RA-{shortId(ticket.id)}
                      </span>
                      <span className="request-preview">
                        {ticket.request_text}
                      </span>
                    </Link>
                  </td>
                  <td>
                    <span className="employee-name">
                      {employees.find(
                        (employee) => employee.id === ticket.employee_id,
                      )?.name ?? ticket.employee_id}
                    </span>
                  </td>
                  <td>
                    <StatusBadge status={ticket.status} />
                  </td>
                  <td>
                    <time dateTime={ticket.created_at}>
                      {dateTime(ticket.created_at)}
                    </time>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="pagination">
        <span>
          {data.total
            ? `${offset + 1}–${Math.min(offset + 10, data.total)} of ${data.total} requests`
            : "0 requests"}
        </span>
        <div>
          <button
            className="button small"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 10))}
          >
            Previous
          </button>
          <button
            className="button small"
            disabled={offset + 10 >= data.total}
            onClick={() => setOffset(offset + 10)}
          >
            Next
          </button>
        </div>
      </div>
    </>
  );
}
