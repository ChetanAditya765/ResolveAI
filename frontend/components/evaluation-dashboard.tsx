"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { FlaskConical, Play, RefreshCw } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { dateTime, shortId } from "@/lib/format";
import { usePoll } from "@/lib/use-poll";
import type {
  EvaluationBatch,
  EvaluationResult,
  EvaluationScenario,
  EvaluationSource,
  EvaluationSummary,
  Page,
} from "@/types/api";
import { EvaluationResultCard } from "./evaluation-evidence";
import { EvaluationMetrics } from "./evaluation-metrics";
import { useSession } from "./session-provider";
import {
  EmptyState,
  ErrorNotice,
  Loading,
  PageHeader,
  StatusBadge,
} from "./ui";

export function EvaluationDashboard({
  source,
  batchId,
}: {
  source: EvaluationSource;
  batchId?: string;
}) {
  const router = useRouter();
  const { session } = useSession();
  const [offset, setOffset] = useState(0);
  const batches = usePoll(
    useCallback(
      async (signal) => {
        const list = await api<Page<EvaluationBatch>>(
          "/evaluations/batches?limit=100",
          { signal },
        );
        const selected = batchId
          ? (list.items.find((batch) => batch.id === batchId) ??
            (await api<EvaluationBatch>(`/evaluations/batches/${batchId}`, {
              signal,
            })))
          : list.items[0];
        return { items: list.items, selected };
      },
      [batchId],
    ),
    3000,
  );
  const catalog = usePoll(
    useCallback(
      (signal) =>
        api<EvaluationScenario[]>("/evaluations/scenarios", { signal }),
      [],
    ),
    0,
  );
  const activeBatch = batches.data?.items.find((batch) =>
    ["PENDING", "RUNNING"].includes(batch.status),
  );
  const selected = batches.data?.selected;
  const selectedId =
    source === "scenario" ? (batchId ?? selected?.id) : undefined;
  return (
    <>
      <PageHeader
        eyebrow="AGENT RELIABILITY"
        title="Evaluation workspace"
        description="Inspect policy controls, approval discipline, and verified outcomes across agent runs."
      >
        {source === "scenario" &&
          session &&
          ["manager", "admin"].includes(session.user.role) && (
            <SubmitEvaluation
              key={session.access_token}
              token={session.access_token}
              count={catalog.data?.length}
              active={Boolean(activeBatch)}
              ready={Boolean(batches.data && !batches.error && !catalog.error)}
              onSubmitted={(batch) =>
                router.push(`/evaluations?source=scenario&batch=${batch.id}`)
              }
            />
          )}
      </PageHeader>
      <div className="tabs evaluation-source" aria-label="Evaluation source">
        <Link
          className={source === "live" ? "active" : ""}
          href="/evaluations"
          aria-current={source === "live" ? "page" : undefined}
        >
          Live agent runs
        </Link>
        <Link
          className={source === "scenario" ? "active" : ""}
          href="/evaluations?source=scenario"
          aria-current={source === "scenario" ? "page" : undefined}
        >
          Scenario suite
        </Link>
      </div>
      <p className="evaluation-source-note">
        {source === "live"
          ? "Every completed run is checked automatically. Task success measures verified resolution; safely escalated requests remain visible as unsuccessful tasks."
          : "Each scenario runs the real workflow in an isolated demo database. Task success means the observed behavior matches the scenario expectations, including required escalations."}
      </p>
      {source === "scenario" && (
        <>
          {(!session || session.user.role === "employee") && (
            <div className="notice">
              Select a manager or administrator identity to run the scenario
              suite.
            </div>
          )}
          {catalog.error && (
            <ErrorNotice message={catalog.error} retry={catalog.refresh} />
          )}
          {batches.error && (
            <ErrorNotice message={batches.error} retry={batches.refresh} />
          )}
          <section
            className="panel evaluation-batch-panel"
            aria-label="Scenario batch"
          >
            <div className="panel-heading">
              <div>
                <h2>Scenario batches</h2>
                <p>
                  {catalog.data
                    ? `${catalog.data.length} versioned scenarios`
                    : "Loading scenario catalog…"}{" "}
                  · live tickets and permissions are isolated
                </p>
              </div>
              <FlaskConical size={20} />
            </div>
            <div className="evaluation-batch-body">
              <label htmlFor="evaluation-batch">Saved batch</label>
              <select
                id="evaluation-batch"
                className="compact-select"
                value={batchId ?? "latest"}
                disabled={!batches.data?.items.length}
                onChange={(event) =>
                  router.push(
                    `/evaluations?source=scenario${event.target.value === "latest" ? "" : `&batch=${event.target.value}`}`,
                  )
                }
              >
                <option value="latest">Latest batch</option>
                {batches.data?.items.map((batch) => (
                  <option key={batch.id} value={batch.id}>
                    {dateTime(batch.created_at)} · {shortId(batch.id)} ·{" "}
                    {batch.status.toLowerCase()}
                  </option>
                ))}
                {selected &&
                  !batches.data?.items.some(
                    (batch) => batch.id === selected.id,
                  ) && (
                    <option value={selected.id}>
                      {dateTime(selected.created_at)} · {shortId(selected.id)}
                    </option>
                  )}
              </select>
              {selected ? (
                <div
                  className="evaluation-batch-progress"
                  data-testid="evaluation-batch-progress"
                >
                  <div>
                    <StatusBadge status={selected.status} />
                    <strong>
                      {selected.completed_count} / {selected.total_count}{" "}
                      scenarios evaluated
                    </strong>
                    <span>
                      {selected.catalog_version} · {selected.evaluator_version}{" "}
                      · {shortId(selected.id)}
                    </span>
                  </div>
                  <progress
                    aria-label="Scenario batch completion"
                    value={selected.completed_count}
                    max={selected.total_count}
                  />
                  {["PENDING", "RUNNING"].includes(selected.status) && (
                    <p>
                      Results are saved after each scenario. Live requests take
                      priority between scenarios.
                    </p>
                  )}
                  {selected.error && <ErrorNotice message={selected.error} />}
                </div>
              ) : (
                <p className="field-help">
                  Run the suite to create a durable batch and inspect its
                  results.
                </p>
              )}
            </div>
          </section>
          {activeBatch && selected?.id !== activeBatch.id && (
            <div className="notice">
              Another batch is active.{" "}
              <Link
                className="text-link"
                href={`/evaluations?source=scenario&batch=${activeBatch.id}`}
              >
                View active batch
              </Link>
            </div>
          )}
        </>
      )}
      <EvaluationResults
        key={`${source}:${selectedId ?? "none"}:${offset}`}
        source={source}
        batchId={selectedId}
        offset={offset}
        setOffset={setOffset}
        catalog={catalog.data ?? []}
      />
      <details className="panel evaluation-methodology">
        <summary>How these scores are calculated</summary>
        <div>
          <p>
            Deterministic assertions check recorded tool arguments and outputs,
            retrieved policy evidence, approval timing, and verification before
            closure. Missing samples display N/A. Rate denominators count
            applicable evaluated runs; tool accuracy is the mean score across
            those runs.
          </p>
          <p>
            Scenario evaluations add expected and forbidden tools, approval
            requirements, final permissions, and exact outcomes. Escalation
            accuracy requires these scenario expectations and is N/A for live
            requests.
          </p>
          <p>
            Active latency sums recorded agent step time and excludes the
            approval boundary. Human wait measures the time from approval
            request to decision. These checks do not assess arbitrary response
            quality with an LLM judge.
          </p>
        </div>
      </details>
    </>
  );
}

function SubmitEvaluation({
  token,
  count,
  active,
  ready,
  onSubmitted,
}: {
  token: string;
  count?: number;
  active: boolean;
  ready: boolean;
  onSubmitted: (batch: EvaluationBatch) => void;
}) {
  const submissionKey = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  async function submit() {
    setBusy(true);
    setError(undefined);
    submissionKey.current ??= crypto.randomUUID();
    try {
      const batch = await api<EvaluationBatch>(
        "/evaluations/run",
        {
          method: "POST",
          body: JSON.stringify({ submission_key: submissionKey.current }),
        },
        token,
      );
      submissionKey.current = null;
      onSubmitted(batch);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="evaluation-submit">
      <button
        className="button primary"
        disabled={busy || active || !ready || !count}
        onClick={() => void submit()}
      >
        <Play size={16} />
        {busy
          ? "Submitting…"
          : active
            ? "Batch in progress"
            : `Run all ${count ?? ""} scenarios`}
      </button>
      {error && <ErrorNotice message={error} />}
    </div>
  );
}

function EvaluationResults({
  source,
  batchId,
  offset,
  setOffset,
  catalog,
}: {
  source: EvaluationSource;
  batchId?: string;
  offset: number;
  setOffset: (offset: number) => void;
  catalog: EvaluationScenario[];
}) {
  const query = `source=${source}${batchId ? `&batch_id=${batchId}` : ""}`;
  const { data, error, refresh } = usePoll(
    useCallback(
      async (signal) => {
        const [summary, results] = await Promise.all([
          api<EvaluationSummary>(`/evaluations/summary?${query}`, { signal }),
          api<Page<EvaluationResult>>(
            `/evaluations/results?${query}&limit=10&offset=${offset}`,
            { signal },
          ),
        ]);
        return { summary, results };
      },
      [query, offset],
    ),
    3000,
  );
  if (!data)
    return error ? (
      <ErrorNotice message={error} retry={refresh} />
    ) : (
      <Loading label="Loading evaluation results…" />
    );
  return (
    <>
      {error && (
        <ErrorNotice
          message={`Refresh failed. Showing the last loaded evaluation. ${error}`}
          retry={refresh}
        />
      )}
      <div
        className="evaluation-score-caption"
        data-testid="evaluation-summary-counts"
      >
        <strong>
          {data.summary.total_results} evaluated{" "}
          {source === "live" ? "runs" : "scenarios"}
        </strong>
        <span>
          {data.summary.passed_results} passed all applicable checks ·{" "}
          {data.summary.evaluator_version}
        </span>
      </div>
      <EvaluationMetrics summary={data.summary} />
      <section
        className="panel evaluation-results"
        aria-label="Saved evaluation results"
      >
        <div className="panel-heading">
          <div>
            <h2>Evaluation results</h2>
            <p>Inspect assertions and the evidence used for each score</p>
          </div>
          <button
            className="button small"
            onClick={refresh}
            aria-label="Refresh evaluation results"
          >
            <RefreshCw size={14} />
            Refresh
          </button>
        </div>
        {data.results.items.length ? (
          data.results.items.map((result) => (
            <EvaluationResultCard
              key={result.id}
              result={result}
              title={
                source === "scenario"
                  ? catalog.find((item) => item.id === result.scenario_id)?.name
                  : undefined
              }
            />
          ))
        ) : (
          <EmptyState
            title="No evaluations yet"
            description={
              source === "live"
                ? "Complete an agent run to see its automatic evaluation here."
                : "Saved scenario results appear here as the worker completes the batch."
            }
          />
        )}
        <div className="pagination">
          <span>
            {data.results.total
              ? `${offset + 1}–${Math.min(offset + 10, data.results.total)} of ${data.results.total} results`
              : "0 results"}
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
              disabled={offset + 10 >= data.results.total}
              onClick={() => setOffset(offset + 10)}
            >
              Next
            </button>
          </div>
        </div>
      </section>
    </>
  );
}
