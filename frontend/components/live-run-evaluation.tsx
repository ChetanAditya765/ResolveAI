"use client";
import Link from "next/link";
import { useCallback } from "react";
import { ChartNoAxesCombined } from "lucide-react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/use-poll";
import type { EvaluationResult, Page } from "@/types/api";
import {
  AssertionList,
  CheckStatus,
  StoredEvaluationTrace,
} from "./evaluation-evidence";
import { percentage } from "./evaluation-metrics";
import { ErrorNotice } from "./ui";

const checks = [
  { name: "policy_evidence", label: "Policy evidence" },
  { name: "approval_before_grant", label: "Approval controls" },
  { name: "tool_scope", label: "Tool scope" },
  { name: "verification_before_close", label: "Verification before closure" },
];
export function LiveRunEvaluation({ runId }: { runId: string }) {
  const { data, error, refresh } = usePoll(
    useCallback(
      (signal) =>
        api<Page<EvaluationResult>>(
          `/evaluations/results?source=live&run_id=${runId}&limit=1`,
          { signal },
        ),
      [runId],
    ),
    5000,
  );
  const result = data?.items[0];
  return (
    <section
      className="panel live-run-evaluation"
      data-testid="run-evaluation"
      aria-label="Automatic run evaluation"
    >
      <div className="panel-heading">
        <div>
          <h2>Run evaluation</h2>
          <p>Automatic checks against the persisted execution trace</p>
        </div>
        <ChartNoAxesCombined size={19} />
      </div>
      {error && <ErrorNotice message={error} retry={refresh} />}
      {!result ? (
        <div className="padded evaluation-pending" role="status">
          <p>
            Evaluation pending. The completed run is queued for deterministic
            checks.
          </p>
          <button className="text-button" onClick={refresh}>
            Refresh evaluation
          </button>
        </div>
      ) : (
        <div className="evaluation-run-body">
          <div className="evaluation-run-summary">
            <div>
              <span className="eyebrow">TASK SUCCESS</span>
              <strong data-testid="run-task-success">
                {percentage(result.metrics.task_success)}
              </strong>
            </div>
            <div>
              <CheckStatus passed={result.passed} />
              <p>{result.evaluator_version}</p>
            </div>
          </div>
          <p className="evaluation-trace-caption">
            Task success requires a verified resolution. A safe escalation can
            pass the safety checks without completing the access request.
          </p>
          <dl className="evaluation-check-list">
            {checks.map(({ name, label }) => {
              const assertion = result.assertions.find(
                (item) => item.name === name,
              );
              return (
                <div key={name} data-testid={`run-check-${name}`}>
                  <dt>{label}</dt>
                  <dd>
                    {assertion ? (
                      <CheckStatus passed={assertion.passed} />
                    ) : (
                      "Not recorded"
                    )}
                  </dd>
                </div>
              );
            })}
          </dl>
          <details className="evaluation-all-checks">
            <summary>
              Inspect all {result.assertions.length} checks
              <Chevron />
            </summary>
            <AssertionList assertions={result.assertions} />
            <StoredEvaluationTrace resultId={result.id} />
          </details>
          <Link href="/evaluations" className="text-link">
            Open evaluation dashboard
          </Link>
        </div>
      )}
    </section>
  );
}

function Chevron() {
  return <span aria-hidden="true">⌄</span>;
}
