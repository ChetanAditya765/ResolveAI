"use client";
import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowRight, FileCheck2, GitBranch, ShieldCheck } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { usePoll } from "@/lib/use-poll";
import type { Employee, Page, Ticket } from "@/types/api";
import { ErrorNotice, Loading, PageHeader } from "./ui";

export function NewTicket() {
  const router = useRouter();
  const { data, error, refresh } = usePoll(
    useCallback(
      (signal) => api<Page<Employee>>("/employees?limit=100", { signal }),
      [],
    ),
    0,
  );
  const [employeeId, setEmployeeId] = useState("EMP001");
  const [request, setRequest] = useState(
    "I need write access to the payments repository.",
  );
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  const [created, setCreated] = useState<Ticket>();
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setFailure(undefined);
    try {
      const ticket =
        created ??
        (await api<Ticket>("/tickets", {
          method: "POST",
          body: JSON.stringify({
            employee_id: employeeId,
            request_text: request.trim(),
          }),
        }));
      setCreated(ticket);
      await api(`/tickets/${ticket.id}/run`, { method: "POST" });
      router.push(`/tickets/${ticket.id}`);
    } catch (failure) {
      setFailure(errorMessage(failure));
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader
        eyebrow="ACCESS REQUEST"
        title="What access do you need?"
        description="Describe the repository and permission. ResolveAI will check policy and coordinate the next step."
      />
      <div className="form-layout">
        <section className="panel form-panel">
          <div className="panel-heading">
            <h2>New ticket</h2>
            <span className="badge neutral">Repository access</span>
          </div>
          {error ? (
            <ErrorNotice message={error} retry={refresh} />
          ) : !data ? (
            <Loading label="Loading employee directory…" />
          ) : (
            <form onSubmit={submit} className="ticket-form">
              <label htmlFor="employee">Employee</label>
              <p id="employee-help" className="input-help">
                Select a directory identity for this demo request.
              </p>
              <select
                id="employee"
                aria-describedby="employee-help"
                value={employeeId}
                onChange={(event) => setEmployeeId(event.target.value)}
                disabled={busy || !!created}
                required
              >
                {data.items
                  .filter((employee) => employee.is_active)
                  .map((employee) => (
                    <option key={employee.id} value={employee.id}>
                      {employee.name} — {employee.department}
                    </option>
                  ))}
              </select>
              <label htmlFor="request">Access request</label>
              <p id="request-help" className="input-help">
                Include the repository name and read, write, or admin
                permission.
              </p>
              <textarea
                id="request"
                aria-describedby="request-help"
                value={request}
                onChange={(event) => setRequest(event.target.value)}
                minLength={3}
                maxLength={8000}
                rows={6}
                required
                disabled={busy || !!created}
              />
              <div className="field-note">
                <span>
                  Requests and agent activity are retained in the ticket.
                </span>
                <span>{request.length}/8000</span>
              </div>
              {failure && <ErrorNotice message={failure} />}
              {created && failure && (
                <div className="notice">
                  Your ticket was saved. Retry starting its agent run, or{" "}
                  <Link href={`/tickets/${created.id}`}>open the ticket</Link>.
                </div>
              )}
              <div className="form-footer">
                <Link className="button" href="/">
                  Cancel
                </Link>
                <button
                  className="button primary"
                  disabled={busy || request.trim().length < 3}
                  type="submit"
                >
                  {busy
                    ? "Starting agent run…"
                    : created
                      ? "Retry agent run"
                      : "Submit request"}
                  <ArrowRight size={16} />
                </button>
              </div>
            </form>
          )}
        </section>
        <aside className="request-guide">
          <div className="eyebrow">WHAT HAPPENS NEXT</div>
          <h2>
            A traceable path
            <br />
            to the right access.
          </h2>
          {[
            {
              icon: GitBranch,
              title: "Identify the request",
              text: "Match the employee and repository with the company directory.",
            },
            {
              icon: ShieldCheck,
              title: "Apply company policy",
              text: "Retrieve relevant policy and request a manager decision when required.",
            },
            {
              icon: FileCheck2,
              title: "Verify the outcome",
              text: "Read access back after execution. Close only when verification confirms success.",
            },
          ].map(({ icon: Icon, title, text }, index) => (
            <div className="guide-step" key={title}>
              <span>
                <Icon size={19} />
              </span>
              <div>
                <small>0{index + 1}</small>
                <h3>{title}</h3>
                <p>{text}</p>
              </div>
            </div>
          ))}
        </aside>
      </div>
    </>
  );
}
