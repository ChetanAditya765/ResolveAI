import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.fixture
def clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)


def test_embedding_dimensions_from_compose_environment(
    monkeypatch: pytest.MonkeyPatch, clean_settings_environment: None
) -> None:
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "1536")

    settings = Settings(_env_file=None)

    assert settings.embedding_dimensions == 1536
    assert type(settings.embedding_dimensions) is int


@pytest.mark.parametrize("value", ["768", "1535", "1537", "3072", "1536.5", "abc", ""])
def test_embedding_dimensions_reject_incompatible_environment_values(
    monkeypatch: pytest.MonkeyPatch, clean_settings_environment: None, value: str
) -> None:
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", value)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert {tuple(item["loc"]) for item in error.value.errors()} == {("embedding_dimensions",)}


def test_embedding_dimensions_default_matches_vector_schema(
    clean_settings_environment: None,
) -> None:
    assert Settings(_env_file=None).embedding_dimensions == 1536
