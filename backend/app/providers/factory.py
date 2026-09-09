from app.core.config import Settings
from app.providers import AgentProvider, DemoProvider, OpenAIProvider, ProviderError


def create_provider(settings: Settings) -> AgentProvider:
    if settings.llm_provider == "demo":
        return DemoProvider()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value().strip():
        raise ProviderError(
            "provider_not_configured", "OPENAI_API_KEY is required for openai mode."
        )
    return OpenAIProvider(
        settings.openai_api_key.get_secret_value(),
        settings.openai_model,
        settings.llm_timeout_seconds,
    )
