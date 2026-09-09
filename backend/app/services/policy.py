"""Evaluate retrieved, version-bound policy evidence against catalog facts."""

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Literal
from uuid import UUID

from app.agents.state import DecisionSummary
from app.models import Permission
from app.rag.contracts import PolicyEvidence, PolicySearch
from app.tools.schemas import EmployeeInfo, RepositoryInfo


@dataclass(frozen=True)
class PolicyRule:
    slug: str
    section: str


READ_RULE = PolicyRule("repository-access-policy", "§2. Read access within a department")
WRITE_RULE = PolicyRule("repository-access-policy", "§3. Write access")
CROSS_DEPARTMENT_RULE = PolicyRule("repository-access-policy", "§4. Cross-department access")
ADMIN_RULE = PolicyRule("repository-access-policy", "§5. Admin access")
EXISTING_RULE = PolicyRule("least-privilege-policy", "§2. Existing permissions")
IDENTITY_RULE = PolicyRule("repository-access-policy", "§1. Scope and identity")
_RANK = {Permission.NONE: 0, Permission.READ: 1, Permission.WRITE: 2, Permission.ADMIN: 3}
# This committed manifest is updated with a reviewed policy/rule change. Do not
# derive authorization trust from editable policy files or database content.
_MANIFEST = json.loads(files("app.services").joinpath("policy_manifest.json").read_text("utf-8"))


def _applicable_rules(
    employee: EmployeeInfo,
    repository: RepositoryInfo,
    requested: Permission,
    current: Permission,
) -> tuple[PolicyRule, ...]:
    if not employee.is_active:
        return (IDENTITY_RULE,)
    if requested == Permission.ADMIN:
        return (ADMIN_RULE,)
    if requested == Permission.NONE:
        return ()
    if _RANK[current] >= _RANK[requested]:
        return (EXISTING_RULE,)
    cross_department = employee.department != repository.owning_department
    if requested == Permission.WRITE:
        return (WRITE_RULE, CROSS_DEPARTMENT_RULE) if cross_department else (WRITE_RULE,)
    return (CROSS_DEPARTMENT_RULE,) if cross_department else (READ_RULE,)


def policy_search_context(
    requested: Permission,
    current: Permission,
    employee: EmployeeInfo,
    repository: RepositoryInfo,
) -> PolicySearch:
    """Build a retrieval query from validated catalog facts and applicable headings."""
    rules = _applicable_rules(employee, repository, requested, current)
    slugs = list(dict.fromkeys(rule.slug for rule in rules)) or ["repository-access-policy"]
    if requested == Permission.ADMIN:
        slugs.append("privileged-access-policy")
    headings = "; ".join(rule.section for rule in rules)
    return PolicySearch(
        query=(
            f"Repository access policy. {headings}. "
            f"Requested permission {requested.value}; current permission {current.value}. "
            f"Employee department {employee.department}; repository department "
            f"{repository.owning_department}; sensitivity {repository.sensitivity_level.value}."
        ),
        document_slugs=slugs,
    )


def _valid_id(value: str) -> bool:
    try:
        return UUID(value).int != 0
    except (ValueError, TypeError, AttributeError):
        return False


def _verified_evidence(evidence: list[PolicyEvidence]) -> dict[PolicyRule, PolicyEvidence]:
    verified: dict[PolicyRule, PolicyEvidence] = {}
    for item in evidence:
        document = _MANIFEST["documents"].get(item.slug)
        if document is None or not _valid_id(item.document_id) or not _valid_id(item.chunk_id):
            continue
        if (
            item.title != document["title"]
            or item.version != document["version"]
            or item.content_hash != document["content_hash"]
        ):
            continue
        section_hash = document["sections"].get(item.section)
        body = item.excerpt.replace("\r\n", "\n").strip()
        if section_hash is None or hashlib.sha256(body.encode("utf-8")).hexdigest() != section_hash:
            continue
        verified.setdefault(PolicyRule(item.slug, item.section), item)
    return verified


def _summary(
    disposition: Literal["grant", "no_change", "approval_required", "escalate"],
    reason: str,
    repository: RepositoryInfo,
    citations: list[PolicyEvidence],
) -> DecisionSummary:
    references = "; ".join(f"{item.title} {item.section}" for item in citations)
    citation_text = f" Under {references}." if references else ""
    return DecisionSummary(
        disposition=disposition,
        summary=(
            f"{reason}{citation_text} "
            f"Catalog repository sensitivity: {repository.sensitivity_level.value}."
        ),
        policy_chunk_ids=list(dict.fromkeys(item.chunk_id for item in citations)),
    )


def has_completion_evidence(evidence: list[PolicyEvidence]) -> bool:
    return EXISTING_RULE in _verified_evidence(evidence)


def decide_access(
    employee: EmployeeInfo,
    repository: RepositoryInfo,
    requested: Permission,
    current: Permission,
    evidence: list[PolicyEvidence],
) -> DecisionSummary:
    """Return a bounded recommendation; this function never changes permissions."""
    rules = _applicable_rules(employee, repository, requested, current)
    verified = _verified_evidence(evidence)
    citations = [verified[rule] for rule in rules if rule in verified]
    if not employee.is_active:
        return _summary(
            "escalate",
            "The employee is inactive; access cannot be authorized.",
            repository,
            citations,
        )
    if requested == Permission.ADMIN:
        return _summary(
            "escalate",
            "Admin access requires Security Administration review; "
            "automated grants are prohibited.",
            repository,
            citations,
        )
    if requested == Permission.NONE:
        return _summary(
            "escalate", "Access removal is outside the supported request workflow.", repository, []
        )
    if not rules or len(citations) != len(rules):
        # Do not attribute the fallback to an absent rule or a distracting document.
        return _summary(
            "escalate",
            "Required policy evidence is missing or unsupported; manual review is required.",
            repository,
            citations,
        )
    if rules == (EXISTING_RULE,):
        return _summary(
            "no_change",
            f"Current {current.value} permission already satisfies the {requested.value} request; "
            "no permission change is needed.",
            repository,
            citations,
        )
    if WRITE_RULE in rules or CROSS_DEPARTMENT_RULE in rules:
        if not employee.manager_id or not employee.manager_id.strip():
            return _summary(
                "escalate",
                "Manager approval is required, but the employee has no recorded manager.",
                repository,
                citations,
            )
        requirement = (
            "New write and cross-department access require"
            if len(rules) == 2
            else "New write access requires"
            if WRITE_RULE in rules
            else "Cross-department read access requires"
        )
        return _summary(
            "approval_required",
            f"{requirement} approval from the employee's recorded manager before any grant.",
            repository,
            citations,
        )
    return _summary(
        "grant",
        "The active employee and repository have the same department; read access is permitted.",
        repository,
        citations,
    )
