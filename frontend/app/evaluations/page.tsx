import { EvaluationDashboard } from "@/components/evaluation-dashboard";

export default async function EvaluationsPage({
  searchParams,
}: {
  searchParams: Promise<{ source?: string; batch?: string }>;
}) {
  const query = await searchParams;
  const source = query.source === "scenario" ? "scenario" : "live";
  const batchId =
    source === "scenario" &&
    typeof query.batch === "string" &&
    /^[a-f0-9-]{36}$/i.test(query.batch)
      ? query.batch
      : undefined;
  return (
    <EvaluationDashboard
      key={`${source}:${batchId ?? "latest"}`}
      source={source}
      batchId={batchId}
    />
  );
}
