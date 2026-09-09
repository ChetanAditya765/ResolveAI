"use client";
import { useCallback } from "react";
import { BookOpen, ChevronDown } from "lucide-react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/use-poll";
import type { Page } from "@/types/api";
import { ErrorNotice, Loading, PageHeader } from "./ui";

interface Policy {
  id: string;
  slug: string;
  title: string;
  version: string;
  content_hash: string;
  content: string;
}
export function PolicyLibrary() {
  const { data, error, refresh } = usePoll(
    useCallback(async (signal) => {
      const sources = await api<Page<Policy>>("/policies?limit=100", {
        signal,
      });
      return Promise.all(
        sources.items.map((source) =>
          api<Policy>(`/policies/${source.id}`, { signal }),
        ),
      );
    }, []),
    0,
  );
  return (
    <>
      <PageHeader
        eyebrow="COMPANY KNOWLEDGE"
        title="Policy library"
        description="The versioned company policies used to ground access decisions and verification."
      />
      {error ? (
        <ErrorNotice message={error} retry={refresh} />
      ) : !data ? (
        <Loading label="Loading policy documents…" />
      ) : (
        <>
          <div className="list-caption">
            <span>{data.length} source documents</span>
            <span>Read-only · Maintained with the application</span>
          </div>
          <div className="policy-library">
            {data.map((policy) => (
              <details className="panel policy-document" key={policy.id}>
                <summary>
                  <span className="icon-tile">
                    <BookOpen size={20} />
                  </span>
                  <div>
                    <h2>{policy.title}</h2>
                    <p>
                      {policy.slug} · Version {policy.version}
                    </p>
                  </div>
                  <ChevronDown size={18} />
                </summary>
                <div className="policy-document-body">
                  <pre>{policy.content}</pre>
                  <div className="evidence-meta">
                    Source fingerprint: {policy.content_hash}
                  </div>
                </div>
              </details>
            ))}
          </div>
        </>
      )}
    </>
  );
}
