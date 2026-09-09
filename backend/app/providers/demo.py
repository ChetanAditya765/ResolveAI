import re

from app.models import Permission
from app.providers.contracts import Classification, RepositoryArguments, RepositoryToolCall

REQUEST = re.compile(
    r"(?:please\s+)?(?:(?:i\s+(?:need|want|request)|request|grant\s+me)\s+)?"
    r"(?P<permission>read|write|admin)\s+access\s+(?:to|for)\s+(?:the\s+)?"
    r"(?:(?:repository|repo)\s+)?(?P<repository>[a-z0-9][a-z0-9_.-]{0,119}?)"
    r"(?:\s+(?:repository|repo))?[.!]?",
    re.IGNORECASE,
)


class DemoProvider:
    """An explicit offline fixture parser, not a replacement for the live language model."""

    provider_name = "demo"
    model_name = "deterministic-access-v1"

    def classify(self, request_text: str) -> Classification:
        match = REQUEST.fullmatch(request_text.strip())
        if match is None:
            intent = "ambiguous" if re.search(r"access|repo", request_text, re.I) else "unsupported"
            return Classification(
                intent=intent,
                repository_name=None,
                requested_permission=None,
                summary="A single repository and an explicit permission could not be determined.",
            )
        repository = match["repository"].lower()
        if repository in {"repo", "repository", "it", "that", "this"}:
            return Classification(
                intent="ambiguous",
                repository_name=None,
                requested_permission=None,
                summary="A repository name is required.",
            )
        return Classification(
            intent="repository_access",
            repository_name=repository,
            requested_permission=Permission(match["permission"].lower()),
            summary="One repository access request was identified.",
        )

    def select_repository_tool(self, repository_name: str) -> RepositoryToolCall:
        return RepositoryToolCall(
            tool_name="find_repository",
            arguments=RepositoryArguments(repository_name=repository_name),
        )
