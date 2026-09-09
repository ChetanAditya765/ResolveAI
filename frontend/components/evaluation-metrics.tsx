import { CheckCheck, Clock3, ShieldCheck, Target } from "lucide-react";
import type { EvaluationSummary } from "@/types/api";

export function percentage(value: number | null | undefined) {
  return value == null ? "N/A" : `${(value * 100).toFixed(1)}%`;
}
export function duration(value: number | null | undefined) {
  if (value == null) return "N/A";
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(1)} s`;
  return `${(value / 60000).toFixed(1)} min`;
}
const rates = [
  { key: "task_success", title: "Task success", icon: Target },
  { key: "policy_compliance", title: "Policy compliance", icon: ShieldCheck },
  { key: "tool_selection_accuracy", title: "Tool accuracy", icon: CheckCheck },
  {
    key: "approval_compliance",
    title: "Approval compliance",
    icon: ShieldCheck,
  },
  {
    key: "escalation_correctness",
    title: "Escalation accuracy",
    icon: CheckCheck,
  },
  {
    key: "hallucination_or_invalid_resource_rate",
    title: "Invalid resource rate",
    icon: Target,
  },
];
export function EvaluationMetrics({ summary }: { summary: EvaluationSummary }) {
  return (
    <section
      className="metrics-grid evaluation-metrics"
      aria-label="Evaluation metrics"
    >
      {rates.map(({ key, title, icon: Icon }) => (
        <div className="metric-card" key={key} data-testid={`metric-${key}`}>
          <div className="metric-label">
            {title}
            <Icon size={17} />
          </div>
          <div className="metric-value">{percentage(summary.metrics[key])}</div>
          <p className="metric-note">
            {summary.metric_samples[key] ?? 0} scored runs
            {key === "hallucination_or_invalid_resource_rate"
              ? " · lower is better"
              : ""}
          </p>
        </div>
      ))}
      <div className="metric-card" data-testid="metric-active-latency">
        <div className="metric-label">
          Average active latency
          <Clock3 size={17} />
        </div>
        <div className="metric-value">
          {duration(summary.average_latency_ms)}
        </div>
        <p className="metric-note">
          Recorded step time · approval wait excluded
        </p>
      </div>
      <div className="metric-card" data-testid="metric-approval-wait">
        <div className="metric-label">
          Average approval wait
          <Clock3 size={17} />
        </div>
        <div className="metric-value">
          {duration(summary.average_human_wait_ms)}
        </div>
        <p className="metric-note">Human decision time, reported separately</p>
      </div>
    </section>
  );
}
