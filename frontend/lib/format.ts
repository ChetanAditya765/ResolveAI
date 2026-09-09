export function humanize(value: string) {
  return value
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (c) => c.toUpperCase());
}
export function shortId(id: string) {
  return id.slice(0, 8).toUpperCase();
}
export function dateTime(value: string) {
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
export function latency(ms: number | null) {
  return ms === null
    ? null
    : ms < 1000
      ? `${ms} ms`
      : `${(ms / 1000).toFixed(1)} s`;
}
