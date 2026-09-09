import math
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import APIError, APITimeoutError

from app.core.config import Settings
from app.rag.contracts import RagError
from app.rag.embeddings import (
    BATCH_SIZE,
    DIMENSIONS,
    LocalHashEmbeddingProvider,
    OpenAIEmbeddingProvider,
    create_embedding_provider,
    embedding_identity,
    validate_embeddings,
)


def vector(first: float = 1.0) -> list[float]:
    return [first] + [0.0] * (DIMENSIONS - 1)


def provider_with_result(data) -> tuple[OpenAIEmbeddingProvider, Mock]:
    call = Mock(return_value=SimpleNamespace(model="test-model", data=data))
    client = SimpleNamespace(embeddings=SimpleNamespace(create=call))
    return OpenAIEmbeddingProvider("unused", "test-model", client=client), call


def test_local_vectors_are_deterministic_normalized_and_lexical() -> None:
    provider = LocalHashEmbeddingProvider()
    texts = ["Write access requires manager approval", "write access requires manager approval"]
    vectors = provider.embed(texts)
    assert vectors[0] == vectors[1] == LocalHashEmbeddingProvider().embed(texts)[0]
    assert len(vectors[0]) == DIMENSIONS
    assert math.sqrt(sum(value * value for value in vectors[0])) == pytest.approx(1)
    related, unrelated = provider.embed(["write access manager approval", "laptop disk repair"])

    def cosine(other: list[float]) -> float:
        return sum(left * right for left, right in zip(vectors[0], other, strict=True))

    assert cosine(related) > cosine(unrelated)


@pytest.mark.parametrize("text", ["", " ", "!!!", None])
def test_local_rejects_unsearchable_inputs(text) -> None:
    with pytest.raises(RagError, match="text|terms"):
        LocalHashEmbeddingProvider().embed([text])


def test_factory_selects_explicit_local_mode() -> None:
    settings = Settings(_env_file=None, embedding_provider="local_hash", openai_api_key=None)
    assert isinstance(create_embedding_provider(settings), LocalHashEmbeddingProvider)
    assert embedding_identity(settings) == ("local_hash", "sha256-lexical-v1")


@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_factory_does_not_fallback_if_live_provider_has_no_credentials(api_key) -> None:
    settings = Settings(_env_file=None, embedding_provider="openai", openai_api_key=api_key)
    assert embedding_identity(settings) == ("openai", "text-embedding-3-small")
    with pytest.raises(RagError) as error:
        create_embedding_provider(settings)
    assert error.value.code == "embedding_not_configured"


def test_openai_orders_indexed_responses_and_requests_float_vectors() -> None:
    provider, call = provider_with_result(
        [
            SimpleNamespace(index=1, embedding=vector(2)),
            SimpleNamespace(index=0, embedding=vector()),
        ]
    )
    assert provider.embed(["first", "second"]) == [vector(), vector(2)]
    call.assert_called_once_with(
        model="test-model", input=["first", "second"], dimensions=1536, encoding_format="float"
    )


def test_openai_batches_without_changing_input_order() -> None:
    provider, call = provider_with_result([])
    call.side_effect = lambda **kwargs: SimpleNamespace(
        model="test-model",
        data=[
            SimpleNamespace(index=index, embedding=vector(float(text)))
            for index, text in enumerate(kwargs["input"])
        ],
    )
    texts = [str(value) for value in range(1, BATCH_SIZE + 3)]
    result = provider.embed(texts)
    assert [row[0] for row in result] == list(range(1, BATCH_SIZE + 3))
    assert call.call_count == 2


@pytest.mark.parametrize("model", ["other-model", None, "", 123])
def test_openai_rejects_mismatched_or_invalid_response_model(model) -> None:
    provider, call = provider_with_result([SimpleNamespace(index=0, embedding=vector())])
    call.return_value.model = model
    with pytest.raises(RagError) as error:
        provider.embed(["policy"])
    assert error.value.code == "invalid_embeddings"
    call.assert_called_once()


def test_openai_rejects_response_without_model_identity() -> None:
    provider, call = provider_with_result([SimpleNamespace(index=0, embedding=vector())])
    del call.return_value.model
    with pytest.raises(RagError) as error:
        provider.embed(["policy"])
    assert error.value.code == "invalid_embeddings"


def test_openai_rejects_model_change_between_batches() -> None:
    provider, call = provider_with_result([])
    call.side_effect = [
        SimpleNamespace(
            model="test-model",
            data=[SimpleNamespace(index=index, embedding=vector()) for index in range(BATCH_SIZE)],
        ),
        SimpleNamespace(
            model="different-model", data=[SimpleNamespace(index=0, embedding=vector())]
        ),
    ]
    with pytest.raises(RagError) as error:
        provider.embed(["policy"] * (BATCH_SIZE + 1))
    assert error.value.code == "invalid_embeddings"
    assert call.call_count == 2


@pytest.mark.parametrize("indices", [[0, 0], [-1], [1], [True], ["0"], []])
def test_openai_rejects_invalid_cardinality_or_indices(indices) -> None:
    provider, _ = provider_with_result(
        [SimpleNamespace(index=index, embedding=vector()) for index in indices]
    )
    with pytest.raises(RagError) as error:
        provider.embed(["one"])
    assert error.value.code == "invalid_embeddings"


@pytest.mark.parametrize(
    "values",
    [
        [],
        [1.0],
        [0.0] * DIMENSIONS,
        vector(float("nan")),
        vector(float("inf")),
        vector(1e50),
        vector(1e-100),
        vector(True),
        vector("1"),
    ],
)
def test_openai_rejects_malformed_vectors(values) -> None:
    provider, _ = provider_with_result([SimpleNamespace(index=0, embedding=values)])
    with pytest.raises(RagError) as error:
        provider.embed(["one"])
    assert error.value.code == "invalid_embeddings"


@pytest.mark.parametrize("kind", ["timeout", "api"])
def test_provider_errors_are_sanitized_without_retry(kind: str) -> None:
    provider, call = provider_with_result([])
    request = httpx.Request("POST", "https://example.invalid/secret")
    call.side_effect = (
        APITimeoutError(request=request)
        if kind == "timeout"
        else APIError("sensitive raw response", request, body={"secret": "sensitive"})
    )
    with pytest.raises(RagError) as error:
        provider.embed(["policy"])
    assert error.value.code == (
        "embedding_timeout" if kind == "timeout" else "embedding_unavailable"
    )
    assert "sensitive" not in str(error.value)
    assert call.call_count == 1


def test_empty_batch_is_a_noop() -> None:
    provider, call = provider_with_result([])
    assert provider.embed([]) == LocalHashEmbeddingProvider().embed([]) == []
    call.assert_not_called()


def test_unexpected_sdk_failure_does_not_expose_raw_details_to_ingestion_cli() -> None:
    provider, call = provider_with_result([])
    call.side_effect = RuntimeError("sensitive raw SDK details")
    with pytest.raises(RagError) as error:
        provider.embed(["policy"])
    assert error.value.code == "embedding_unavailable"
    assert "sensitive" not in str(error.value)
    assert error.value.__suppress_context__


def test_vector_validator_rejects_missing_batch() -> None:
    with pytest.raises(RagError):
        validate_embeddings(None, count=1, dimensions=DIMENSIONS)
