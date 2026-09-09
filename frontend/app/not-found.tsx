import { EmptyState } from "@/components/ui";
export default function NotFound() {
  return (
    <EmptyState
      title="Page not found"
      description="This page is not available in the workspace."
      href="/"
      action="Return to overview"
    />
  );
}
