"use client";
import Link from "next/link";
import { useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  CircleMinus,
  Code2,
  XCircle,
} from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { dateTime, humanize, shortId } from "@/lib/format";
import type {
  EvaluationAssertion,
  EvaluationDetail,
  EvaluationResult,
} from "@/types/api";
import { duration } from "./evaluation-metrics";
import { ErrorNotice, StatusBadge } from "./ui";

export function CheckStatus({ passed }: { passed: boolean | null }) {
  return (
    <span
      className={`evaluation-check-status ${passed === null ? "neutral" : passed ? "success" : "danger"}`}
    >
      {passed === null ? (
        <CircleMinus size={15} />
      ) : passed ? (
        <CheckCircle2 size={15} />
      ) : (
        <XCircle size={15} />
      )}
      {passed === null ? "Not applicable" : passed ? "Passed" : "Failed"}
    </span>
  );
}

export function AssertionList({
  assertions,
}: {
  assertions: EvaluationAssertion[];
}) {
  return (
    <div className="evaluation-assertions">
      {assertions.map((assertion) => (
        <details className="evaluation-assertion" key={assertion.name}>
          <summary>
            <span>{humanize(assertion.name)}</span>
            <CheckStatus passed={assertion.passed} />
            <ChevronDown size={14} />
          </summary>
          <div className="evaluation-assertion-body">
            <p>{assertion.summary}</p>
            {(assertion.expected != null || assertion.observed != null) && (
              <div className="evaluation-comparison">
                <div>
                  <h3>Expected</h3>
                  <pre>{JSON.stringify(assertion.expected, null, 2)}</pre>
                </div>
                <div>
                  <h3>Observed</h3>
                  <pre>{JSON.stringify(assertion.observed, null, 2)}</pre>
                </div>
              </div>
            )}
          </div>
        </details>
      ))}
    </div>
  );
}

export function StoredEvaluationTrace({ resultId }: { resultId: string }) {
  const [data, setData] = useState<EvaluationDetail>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  async function load() {
    setBusy(true);
    setError(undefined);
    try {
      setData(await api<EvaluationDetail>(`/evaluations/results/${resultId}`));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="stored-evaluation-trace">
      {error && <ErrorNotice message={error} retry={() => void load()} />}
      {!data ? (
        <button
          className="button small"
          onClick={() => void load()}
          disabled={busy}
        >
          <Code2 size={15} />
          {busy ? "Loading stored trace…" : "Inspect stored trace"}
        </button>
      ) : (
        <details className="tool-call" open>
          <summary>
            <Code2 size={16} />
            <span>Stored evaluation trace</span>
            <ChevronDown size={14} />
          </summary>
          <div className="tool-body">
            <p className="evaluation-trace-caption">
              Saved evidence used by {data.evaluator_version}. Structured
              decisions, tool records, and approval history.
            </p>
            {data.expected && (
              <>
                <h3>Scenario expectations</h3>
                <pre>{JSON.stringify(data.expected, null, 2)}</pre>
              </>
            )}
            <h3>Run and evidence</h3>
            <pre data-testid="stored-evaluation-trace">
              {JSON.stringify(data.trace, null, 2)}
            </pre>
          </div>
        </details>
      )}
    </div>
  );
}

export function EvaluationResultCard({
  result,
  title,
}: {
  result: EvaluationResult;
  title?: string;
}) {
  const applicable = result.assertions.filter(
    (assertion) => assertion.passed !== null,
  );
  const passed = applicable.filter((assertion) => assertion.passed).length;
  return (
    <details
      className="evaluation-result"
      data-testid={`evaluation-result-${result.id}`}
    >
      <summary>
        <div className="evaluation-result-title">
          <span className="eyebrow">
            {result.source === "scenario"
              ? "SCENARIO"
              : `RUN ${shortId(result.run_id ?? result.id)}`}
          </span>
          <h3>
            {title ??
              result.observed.request_text ??
              humanize(result.scenario_id)}
          </h3>
          <p>
            {result.observed.employee_name ?? "Employee lookup incomplete"} ·{" "}
            {dateTime(result.created_at)}
          </p>
        </div>
        <div className="evaluation-result-score">
          <CheckStatus passed={result.passed} />
          <span>
            {passed}/{applicable.length} checks · {duration(result.latency_ms)}{" "}
            active
          </span>
        </div>
        <ChevronDown size={17} />
      </summary>
      <div className="evaluation-result-body">
        <div className="evaluation-result-context">
          {result.observed.ticket_status && (
            <StatusBadge status={result.observed.ticket_status} />
          )}
          <span>
            {humanize(result.observed.outcome ?? "Outcome unavailable")}
          </span>
          {result.source === "live" && result.observed.ticket_id && (
            <Link
              className="text-link"
              href={`/tickets/${result.observed.ticket_id}`}
            >
              Open ticket
            </Link>
          )}
          <span className="muted">{result.evaluator_version}</span>
        </div>
        {result.source === "scenario" && (
          <p className="evaluation-trace-caption">
            {result.observed.request_text}
          </p>
        )}
        <AssertionList assertions={result.assertions} />
        <StoredEvaluationTrace resultId={result.id} />
      </div>
    </details>
  );
}
