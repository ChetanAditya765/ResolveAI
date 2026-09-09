from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class RagError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class PolicyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    chunk_id: str
    slug: str = ""  # Empty only for reading historical Phase 2 state.
    title: str
    version: str
    section: str
    excerpt: str
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    content_hash: str


class PolicySearch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=20)
    document_slugs: list[str] = Field(default_factory=list, max_length=8)


class PolicySearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: list[PolicyEvidence]
    embedding_provider: str
    embedding_model: str
    dimensions: int
