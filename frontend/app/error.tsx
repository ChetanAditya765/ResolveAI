"use client";
import { ErrorNotice } from "@/components/ui";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <ErrorNotice
      message="This view could not be loaded. Please retry."
      retry={reset}
    />
  );
}
