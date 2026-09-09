import { BookOpen, ChevronDown } from "lucide-react";
import type { Evidence } from "@/types/api";

export function PolicyEvidence({
  evidence,
  compact = false,
}: {
  evidence: Evidence[];
  compact?: boolean;
}) {
  if (!evidence.length)
    return (
      <p className="muted padded">No policy evidence has been retrieved yet.</p>
    );
  return (
    <div className={`evidence-list ${compact ? "compact" : ""}`}>
      {evidence.map((item) => (
        <details className="evidence-item" key={item.chunk_id}>
          <summary>
            <BookOpen size={16} />
            <span>
              <strong>{item.title}</strong>
              <small>
                {item.section} · v{item.version}
              </small>
            </span>
            <ChevronDown size={14} />
          </summary>
          <div className="evidence-body">
            <p>{item.excerpt}</p>
            <div className="evidence-meta">
              Retrieval score {item.score.toFixed(3)}
              <br />
              Source {item.document_id}
              <br />
              Chunk {item.chunk_id}
            </div>
          </div>
        </details>
      ))}
    </div>
  );
}
