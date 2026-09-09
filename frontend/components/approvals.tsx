"use client";
import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/use-poll";
import type { Approval, Page } from "@/types/api";
import { useSession } from "./session-provider";
import { ApprovalCard } from "./approval-card";
import { EmptyState, ErrorNotice, Loading, PageHeader } from "./ui";

export function Approvals() {
  const { session, loading } = useSession();
  return (
    <>
      <PageHeader
        eyebrow="HUMAN REVIEW"
        title="Approval inbox"
        description="Review the employee, requested action, and policy before recording a decision."
      />
      {loading ? (
        <Loading label="Loading reviewer identity…" />
      ) : !session ? (
        <EmptyState
          title="Select a demo identity"
          description="Choose an identity in the top bar to see requests you can review."
        />
      ) : (
        <ApprovalInbox
          key={session.access_token}
          token={session.access_token}
        />
      )}
    </>
  );
}
function ApprovalInbox({ token }: { token: string }) {
  const [status, setStatus] = useState("PENDING");
  const [offset, setOffset] = useState(0);
  return (
    <>
      <div className="tabs" aria-label="Approval status">
        {[
          { value: "PENDING", label: "Pending review" },
          { value: "APPROVED", label: "Approved" },
          { value: "REJECTED", label: "Rejected" },
        ].map((tab) => (
          <button
            className={status === tab.value ? "active" : ""}
            aria-pressed={status === tab.value}
            key={tab.value}
            onClick={() => {
              setStatus(tab.value);
              setOffset(0);
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <ApprovalList
        key={`${status}:${offset}`}
        token={token}
        status={status}
        offset={offset}
        setOffset={setOffset}
      />
    </>
  );
}
function ApprovalList({
  token,
  status,
  offset,
  setOffset,
}: {
  token: string;
  status: string;
  offset: number;
  setOffset: (offset: number) => void;
}) {
  const { data, error, refresh } = usePoll(
    useCallback(
      (signal) =>
        api<Page<Approval>>(
          `/approvals?status=${status}&limit=10&offset=${offset}`,
          { signal },
          token,
        ),
      [status, token, offset],
    ),
  );
  if (error) return <ErrorNotice message={error} retry={refresh} />;
  if (!data) return <Loading label="Loading approvals…" />;
  return (
    <>
      <div className="list-caption">
        <span>
          {data.total}{" "}
          {status === "PENDING"
            ? "requests awaiting a decision"
            : "recorded decisions"}
        </span>
        <span>Visible to your current identity</span>
      </div>
      {data.items.length ? (
        <div className="approval-list">
          {data.items.map((approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              onDecision={refresh}
            />
          ))}
        </div>
      ) : (
        <section className="panel">
          <EmptyState
            title={
              status === "PENDING"
                ? "No pending approvals"
                : "No decisions in this view"
            }
            description="Requests appear here when policy requires a review and your identity can access the approval."
            href="/"
            action="View request queue"
          />
        </section>
      )}
      <div className="pagination">
        <span>
          {data.total
            ? `${offset + 1}–${Math.min(offset + 10, data.total)} of ${data.total}`
            : "0 approvals"}
        </span>
        <div>
          <button
            className="button small"
            disabled={!offset}
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
