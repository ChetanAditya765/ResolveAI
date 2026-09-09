import hashlib
import math
import re
import struct
from collections import Counter
from numbers import Real
from typing import Any

from openai import APIError, APITimeoutError, OpenAI

from app.core.config import Settings
from app.rag.contracts import EmbeddingProvider, RagError

DIMENSIONS = 1536
BATCH_SIZE = 64


def validate_embeddings(vectors: Any, *, count: int, dimensions: int) -> list[list[float]]:
    """Reject malformed vectors before they reach storage or a distance query."""
    try:
        if len(vectors) != count:
            raise ValueError("Unexpected vector count")
        result: list[list[float]] = []
        for vector in vectors:
            if len(vector) != dimensions:
                raise ValueError("Unexpected dimensions")
            if any(isinstance(value, bool) or not isinstance(value, Real) for value in vector):
                raise ValueError("Nonnumeric vector")
            values = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in values) or not any(values):
                raise ValueError("Nonfinite or zero vector")
            # pgvector stores float32 values; reject overflow before database adaptation.
            if any(abs(value) > 3.402823466e38 for value in values):
                raise ValueError("Vector value exceeds float32")
            values = list(struct.unpack(f"{dimensions}f", struct.pack(f"{dimensions}f", *values)))
            if not any(values):
                raise ValueError("Vector underflows to zero in float32 storage")
            result.append(values)
        return result
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise RagError(
            "invalid_embeddings", "The embedding provider returned invalid vectors."
        ) from None


def _validate_texts(texts: list[str]) -> None:
    if not isinstance(texts, list) or any(
        not isinstance(text, str) or not text.strip() for text in texts
    ):
        raise RagError("invalid_embedding_input", "Embedding inputs must be nonempty text.")


class LocalHashEmbeddingProvider:
    """Offline lexical vectors for the seeded demo, not a semantic embedding model."""

    provider_name = "local_hash"
    model_name = "sha256-lexical-v1"
    dimensions = DIMENSIONS

    def embed(self, texts: list[str]) -> list[list[float]]:
        _validate_texts(texts)
        vectors = []
        for text in texts:
            tokens = re.findall(r"\w+", text.casefold(), flags=re.UNICODE)
            if not tokens:
                raise RagError("invalid_embedding_input", "Text has no searchable terms.")
            features = Counter(f"word:{token}" for token in tokens)
            bigrams = Counter(
                f"pair:{left} {right}" for left, right in zip(tokens, tokens[1:], strict=False)
            )
            features.update(bigrams)
            vector = [0.0] * self.dimensions
            for feature, count in sorted(features.items()):
                digest = hashlib.sha256(feature.encode("utf-8")).digest()
                index = int.from_bytes(digest[:8], "big") % self.dimensions
                sign = 1.0 if digest[8] & 1 else -1.0
                weight = 0.5 if feature.startswith("pair:") else 1.0
                vector[index] += sign * weight * (1 + math.log(count))
            norm = math.sqrt(sum(value * value for value in vector))
            if not norm:
                raise RagError("invalid_embedding_input", "Text has no searchable terms.")
            vectors.append([value / norm for value in vector])
        return validate_embeddings(vectors, count=len(texts), dimensions=self.dimensions)


class OpenAIEmbeddingProvider:
    provider_name = "openai"
    dimensions = DIMENSIONS

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 20,
        *,
        client=None,
    ) -> None:
        self.model_name = model
        self.client = client or OpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        _validate_texts(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = texts[start : start + BATCH_SIZE]
            try:
                response = self.client.embeddings.create(
                    model=self.model_name,
                    input=batch,
                    dimensions=self.dimensions,
                    encoding_format="float",
                )
                if response.model != self.model_name:
                    raise ValueError("Unexpected embedding model")
                indexed: dict[int, list[float]] = {}
                for item in response.data:
                    if type(item.index) is not int or item.index in indexed:
                        raise ValueError("Invalid vector index")
                    indexed[item.index] = item.embedding
                if set(indexed) != set(range(len(batch))):
                    raise ValueError("Missing or out-of-range vector index")
                ordered = [indexed[index] for index in range(len(batch))]
                vectors.extend(
                    validate_embeddings(ordered, count=len(batch), dimensions=self.dimensions)
                )
            except APITimeoutError:
                raise RagError("embedding_timeout", "The embedding request timed out.") from None
            except APIError:
                raise RagError(
                    "embedding_unavailable", "The embedding provider is unavailable."
                ) from None
            except (AttributeError, TypeError, ValueError, KeyError):
                raise RagError(
                    "invalid_embeddings", "The embedding provider returned invalid vectors."
                ) from None
            except RagError:
                raise
            except Exception:
                # Protect command-line ingestion as well as audited tool execution.
                raise RagError(
                    "embedding_unavailable", "The embedding provider is unavailable."
                ) from None
        return vectors


def embedding_identity(settings: Settings) -> tuple[str, str]:
    if settings.embedding_provider == "local_hash":
        return LocalHashEmbeddingProvider.provider_name, LocalHashEmbeddingProvider.model_name
    return "openai", settings.embedding_model


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "local_hash":
        return LocalHashEmbeddingProvider()
    secret = settings.openai_api_key
    if secret is None or not secret.get_secret_value().strip():
        raise RagError("embedding_not_configured", "OpenAI embeddings require an API key.")
    return OpenAIEmbeddingProvider(
        secret.get_secret_value(), settings.embedding_model, settings.embedding_timeout_seconds
    )
