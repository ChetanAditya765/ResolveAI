import re
from dataclasses import dataclass

from app.rag.contracts import RagError

CHUNKER_VERSION = "markdown-sections-v1"


@dataclass(frozen=True)
class Chunk:
    index: int
    section: str
    content: str


def _split_body(body: str, max_chars: int, overlap: int) -> list[str]:
    chunks = []
    start = 0
    while start < len(body):
        end = min(start + max_chars, len(body))
        if end < len(body):
            # Prefer a paragraph or word boundary without creating tiny fragments.
            boundary = body.rfind("\n\n", start + max_chars // 2, end)
            if boundary == -1:
                boundary = body.rfind(" ", start + max_chars // 2, end)
            if boundary != -1:
                end = boundary
        content = body[start:end].strip()
        if content:
            chunks.append(content)
        if end == len(body):
            break
        start = max(start + 1, end - overlap)
    return chunks


def chunk_document(markdown: str, *, max_chars: int = 1800, overlap: int = 150) -> list[Chunk]:
    """Index policy section bodies; document headers remain source metadata."""
    if not 200 <= max_chars <= 1800 or not 0 <= overlap <= min(150, max_chars // 2):
        raise ValueError("Chunk size must be 200..1800; overlap must be 0..150.")
    source = markdown.replace("\r\n", "\n")
    headings = list(re.finditer(r"^##[ \t]+(.+?)[ \t]*$", source, flags=re.MULTILINE))
    chunks: list[Chunk] = []
    for position, heading in enumerate(headings):
        end = headings[position + 1].start() if position + 1 < len(headings) else len(source)
        body = source[heading.end() : end].strip()
        for content in _split_body(body, max_chars, overlap):
            chunks.append(Chunk(index=len(chunks), section=heading.group(1), content=content))
    if not chunks:
        raise RagError("invalid_policy_document", "A policy must contain nonempty H2 sections.")
    return chunks
