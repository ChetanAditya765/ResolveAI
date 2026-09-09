"use client";
import Link from "next/link";
import { useState } from "react";
import { Check, GitBranch, ShieldCheck, X } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { dateTime, shortId } from "@/lib/format";
import type { Approval } from "@/types/api";
import { useSession } from "./session-provider";
import { ErrorNotice, StatusBadge } from "./ui";
import { PolicyEvidence } from "./policy-evidence";

export function ApprovalCard({
  approval,
  onDecision,
}: {
  approval: Approval;
  onDecision: () => void;
}) {
  const { session, loading } = useSession();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [recorded, setRecorded] = useState<Approval>();
  const item = recorded ?? approval;
  async function decide(action: "approve" | "reject") {
    if (!session || busy || !item.can_decide || item.status !== "PENDING")
      return;
    setBusy(true);
    setError(undefined);
    try {
      const result = await api<Approval>(
        `/approvals/${item.id}/${action}`,
        {
          method: "POST",
          body: JSON.stringify({ comment: comment.trim() || null }),
        },
        session.access_token,
      );
      setRecorded(result);
      onDecision();
    } catch (failure) {
      setError(errorMessage(failure));
      onDecision();
    } finally {
      setBusy(false);
    }
  }
  return (
    <article
      className="panel approval-card"
      data-testid={`approval-${item.ticket_id}`}
    >
      <div className="panel-heading">
        <div className="approval-heading">
          <span className="approval-icon">
            <ShieldCheck size={20} />
          </span>
          <div>
            <h2>{item.employee.name}</h2>
            <p>
              {item.employee.department} · {item.employee.email}
            </p>
          </div>
        </div>
        <StatusBadge status={item.status} />
      </div>
      <div className="approval-body">
        <div className="approval-action">
          <span className="repo-icon">
            <GitBranch size={20} />
          </span>
          <div>
            <span className="eyebrow">REQUESTED ACTION</span>
            <h3>
              Grant <strong>{item.permission}</strong> access to{" "}
              <strong>{item.repository.name}</strong>
            </h3>
          </div>
        </div>
        <dl className="approval-facts">
          <div>
            <dt>Assigned reviewer</dt>
            <dd>{item.approver.name}</dd>
          </div>
          <div>
            <dt>Requested</dt>
            <dd>{dateTime(item.requested_at)}</dd>
          </div>
          <div>
            <dt>Ticket</dt>
            <dd>
              <Link href={`/tickets/${item.ticket_id}`}>
                RA-{shortId(item.ticket_id)}
              </Link>
            </dd>
          </div>
          <div>
            <dt>Sensitivity</dt>
            <dd className="capitalize">{item.repository.sensitivity_level}</dd>
          </div>
        </dl>
        <div className="decision-summary">
          <span className="eyebrow">AGENT RECOMMENDATION</span>
          <p>{item.recommendation}</p>
        </div>
        <h3 className="section-label">Relevant policy</h3>
        <PolicyEvidence evidence={item.policy_evidence} compact />
        {item.status === "PENDING" ? (
          item.can_decide ? (
            <div className="approval-controls">
              <label htmlFor={`comment-${item.id}`}>
                Decision comment <span className="muted">(optional)</span>
              </label>
              <textarea
                id={`comment-${item.id}`}
                rows={2}
                maxLength={2000}
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                disabled={busy || loading}
                placeholder="Record the business need or reason for rejection."
              />
              {error && <ErrorNotice message={error} />}
              <div className="approval-buttons">
                <button
                  className="button reject"
                  disabled={busy || loading}
                  onClick={() => void decide("reject")}
                >
                  <X size={16} />
                  Reject request
                </button>
                <button
                  className="button primary"
                  disabled={busy || loading}
                  onClick={() => void decide("approve")}
                >
                  <Check size={16} />
                  {busy ? "Saving decision…" : "Approve access"}
                </button>
              </div>
              <p className="field-help">
                Approval authorizes this exact action. The agent must still
                execute and verify access.
              </p>
            </div>
          ) : (
            <div className="notice">
              Awaiting the assigned manager or an authorized administrator.
              Requesters cannot approve their own access.
            </div>
          )
        ) : (
          <div
            className={`notice ${item.status === "APPROVED" ? "success" : ""}`}
          >
            <div>
              <strong>
                {item.status === "APPROVED"
                  ? "Approval recorded"
                  : "Rejection recorded"}
              </strong>
              <p>
                {item.decided_at && dateTime(item.decided_at)} · Decision actor{" "}
                {item.decided_by_id
                  ? shortId(item.decided_by_id)
                  : "unavailable"}
              </p>
              {item.decision_comment && <p>{item.decision_comment}</p>}
              <Link href={`/tickets/${item.ticket_id}`}>
                View execution and final outcome →
              </Link>
            </div>
          </div>
        )}
      </div>
    </article>
  );
}
