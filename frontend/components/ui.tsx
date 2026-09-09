import { AlertCircle, ArrowRight, Inbox } from "lucide-react";
import Link from "next/link";
import { humanize } from "@/lib/format";

export function StatusBadge({ status }: { status: string }) {
  const tone = ["RESOLVED", "COMPLETED", "SUCCEEDED", "APPROVED"].includes(
    status,
  )
    ? "success"
    : ["WAITING_FOR_APPROVAL", "WAITING", "PENDING"].includes(status)
      ? "warning"
      : ["FAILED", "REJECTED"].includes(status)
        ? "danger"
        : ["PROCESSING", "RUNNING"].includes(status)
          ? "info"
          : "neutral";
  return (
    <span className={`badge ${tone}`}>
      <span className="status-dot" />
      {humanize(status)}
    </span>
  );
}
export function ErrorNotice({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="notice error" role="alert">
      <AlertCircle size={18} />
      <span>{message}</span>
      {retry && (
        <button className="text-button" onClick={retry}>
          Retry
        </button>
      )}
    </div>
  );
}
export function Loading({ label = "Loading workspace…" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" />
      {label}
    </div>
  );
}
export function EmptyState({
  title,
  description,
  href,
  action,
}: {
  title: string;
  description: string;
  href?: string;
  action?: string;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <Inbox size={25} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {href && (
        <Link href={href} className="button primary">
          {action}
          <ArrowRight size={16} />
        </Link>
      )}
    </div>
  );
}
export function PageHeader({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {children}
    </div>
  );
}
