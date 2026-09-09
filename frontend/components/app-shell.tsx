"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback } from "react";
import {
  Activity,
  ArrowUpRight,
  BookOpen,
  ChartNoAxesCombined,
  CheckCheck,
  ChevronDown,
  GitBranch,
  LayoutDashboard,
  Plus,
  ShieldCheck,
} from "lucide-react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/use-poll";
import { useSession } from "./session-provider";

const navigation = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/tickets/new", label: "New ticket", icon: Plus },
  { href: "/approvals", label: "Approvals", icon: ShieldCheck },
  { href: "/policies", label: "Policy library", icon: BookOpen },
  { href: "/evaluations", label: "Evaluations", icon: ChartNoAxesCombined },
];
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const identity = useSession();
  const health = usePoll(
    useCallback((signal) => api<{ status: string }>("/ready", { signal }), []),
    15000,
  );
  return (
    <div className="workspace">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link href="/" className="brand" aria-label="ResolveAI home">
          <span className="brand-mark">
            <GitBranch size={23} strokeWidth={2.4} />
          </span>
          Resolve<span className="brand-ai">AI</span>
          <span className="version">0.1</span>
        </Link>
        <div className="workspace-label">
          <span className="workspace-avatar">N</span>
          <div>
            Northstar<span>IT operations workspace</span>
          </div>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={`nav-item ${pathname === href ? "active" : ""}`}
              aria-current={pathname === href ? "page" : undefined}
            >
              <Icon size={18} />
              {label}
              {pathname === href && <span className="nav-indicator" />}
            </Link>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="guard-label">
            <CheckCheck size={17} /> Governed by policy
          </div>
          <p>
            Every access change requires an auditable decision and verification.
          </p>
          <a
            href="https://github.com/langchain-ai/langgraph"
            target="_blank"
            rel="noreferrer"
            className="sidebar-link"
          >
            Built with LangGraph <ArrowUpRight size={13} />
          </a>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="topbar-location">
            <span>Workspace</span>
            <span className="slash">/</span>
            <strong>
              {pathname.startsWith("/tickets/")
                ? "Access requests"
                : (navigation.find((item) => item.href === pathname)?.label ??
                  "ResolveAI")}
            </strong>
          </div>
          <div className="topbar-actions">
            <span className={`service-state ${health.error ? "offline" : ""}`}>
              <span className="status-dot" />
              {health.error
                ? "Service unavailable"
                : health.data
                  ? "Service connected"
                  : "Connecting"}
            </span>
            <div className="identity-switch">
              <label htmlFor="demo-identity">Demo identity</label>
              <div className="identity-input">
                <select
                  id="demo-identity"
                  value={identity.session?.user.id ?? ""}
                  disabled={identity.loading || !identity.users.length}
                  onChange={(event) =>
                    void identity.switchUser(event.target.value)
                  }
                >
                  <option value="" disabled>
                    {identity.loading ? "Loading…" : "Select identity"}
                  </option>
                  {identity.users.map((user) => (
                    <option key={user.id} value={user.id}>
                      {user.name} · {user.role}
                    </option>
                  ))}
                </select>
                <ChevronDown size={13} />
              </div>
            </div>
          </div>
        </header>
        {identity.error && (
          <div className="session-notice" role="status">
            {identity.error}{" "}
            <button className="text-button" onClick={identity.retry}>
              Retry identity connection
            </button>
          </div>
        )}
        <main id="main-content" className="main-content">
          {children}
        </main>
        <footer className="footer">
          <span>
            <Activity size={13} /> ResolveAI · Access operations
          </span>
          <span>Demo workspace · Simulated repositories</span>
        </footer>
      </div>
    </div>
  );
}
