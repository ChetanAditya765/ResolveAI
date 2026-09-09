"""Idempotent demo records and policy sources; run with ``python -m app.db.seed``."""

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import (
    Employee,
    KnowledgeChunk,
    KnowledgeDocument,
    Permission,
    Repository,
    RepositoryPermission,
    RepositorySensitivity,
    User,
    UserRole,
)
from app.rag.contracts import EmbeddingProvider, RagError


@dataclass(frozen=True)
class EmployeeSeed:
    id: str
    name: str
    email: str
    department: str
    role: str
    manager_id: str | None
    is_active: bool = True


@dataclass(frozen=True)
class RepositorySeed:
    name: str
    department: str
    sensitivity: RepositorySensitivity


@dataclass(frozen=True)
class PolicySeed:
    slug: str
    title: str
    version: str
    content: str
    content_hash: str
    metadata: dict[str, str]


EMPLOYEES = (
    EmployeeSeed(
        "EMP001",
        "Chetan Aditya",
        "chetan@northstar.example",
        "Engineering",
        "Software Engineer",
        "EMP002",
    ),
    EmployeeSeed(
        "EMP002",
        "Maya Rao",
        "maya.rao@northstar.example",
        "Engineering",
        "Engineering Manager",
        "EMP009",
    ),
    EmployeeSeed(
        "EMP003",
        "Arjun Sethi",
        "arjun.sethi@northstar.example",
        "Security",
        "Security Administrator",
        "EMP009",
    ),
    EmployeeSeed(
        "EMP004",
        "Nisha Mehta",
        "nisha.mehta@northstar.example",
        "Finance",
        "Finance Analyst",
        "EMP005",
    ),
    EmployeeSeed(
        "EMP005",
        "Kabir Shah",
        "kabir.shah@northstar.example",
        "Finance",
        "Finance Manager",
        "EMP009",
    ),
    EmployeeSeed(
        "EMP006",
        "Leena Das",
        "leena.das@northstar.example",
        "HR",
        "People Operations Manager",
        "EMP009",
    ),
    EmployeeSeed(
        "EMP007",
        "Rahul Iyer",
        "rahul.iyer@northstar.example",
        "Engineering",
        "Platform Engineer",
        "EMP002",
    ),
    EmployeeSeed(
        "EMP008",
        "Sofia Khan",
        "sofia.khan@northstar.example",
        "IT",
        "IT Support Specialist",
        "EMP003",
    ),
    EmployeeSeed(
        "EMP009", "Dev Patel", "dev.patel@northstar.example", "Engineering", "VP Engineering", None
    ),
    EmployeeSeed(
        "EMP010",
        "Tara Menon",
        "tara.menon@northstar.example",
        "Engineering",
        "Former Contractor",
        "EMP002",
        False,
    ),
)

REPOSITORIES = (
    RepositorySeed("payments", "Engineering", RepositorySensitivity.CONFIDENTIAL),
    RepositorySeed("platform-api", "Engineering", RepositorySensitivity.INTERNAL),
    RepositorySeed("finance-reporting", "Finance", RepositorySensitivity.CONFIDENTIAL),
    RepositorySeed("people-ops", "HR", RepositorySensitivity.CONFIDENTIAL),
    RepositorySeed("security-audit", "Security", RepositorySensitivity.RESTRICTED),
)

DEMO_USERS = (
    ("EMP001", UserRole.EMPLOYEE),
    ("EMP002", UserRole.MANAGER),
    ("EMP003", UserRole.ADMIN),
    ("EMP004", UserRole.EMPLOYEE),
    ("EMP005", UserRole.MANAGER),
    ("EMP006", UserRole.MANAGER),
    ("EMP009", UserRole.MANAGER),
)

INITIAL_PERMISSIONS = (
    ("EMP001", "payments", Permission.READ),
    ("EMP001", "platform-api", Permission.READ),
    ("EMP002", "payments", Permission.WRITE),
    ("EMP003", "security-audit", Permission.ADMIN),
    ("EMP004", "finance-reporting", Permission.READ),
    ("EMP005", "finance-reporting", Permission.WRITE),
    ("EMP006", "people-ops", Permission.WRITE),
    ("EMP007", "platform-api", Permission.WRITE),
    ("EMP009", "platform-api", Permission.WRITE),
    ("EMP010", "payments", Permission.READ),
)


def _demo_id(kind: str, key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://resolveai.local/demo/{kind}/{key}")


def _read_policies(policy_directory: Path) -> list[PolicySeed]:
    paths = sorted(policy_directory.glob("*.md"))
    if not paths:
        raise ValueError(f"No policy markdown files found in {policy_directory}")
    policies = []
    for path in paths:
        content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        lines = content.splitlines()
        if not lines or not lines[0].startswith("# "):
            raise ValueError(f"Policy {path.name} must start with an H1 title")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if line.startswith("## "):
                break
            if ": " in line:
                key, value = line.split(": ", 1)
                headers[key.lower().replace(" ", "_")] = value.strip()
        if not headers.get("version"):
            raise ValueError(f"Policy {path.name} must declare Version")
        policies.append(
            PolicySeed(
                slug=path.stem,
                title=lines[0][2:].strip(),
                version=headers.pop("version"),
                content=content,
                content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                metadata={"source": f"policies/{path.name}", **headers},
            )
        )
    return policies


def _seed_employees(session: Session) -> int:
    created: list[tuple[Employee, EmployeeSeed]] = []
    for data in EMPLOYEES:
        if session.get(Employee, data.id) is not None:
            continue
        employee = Employee(
            id=data.id,
            name=data.name,
            email=data.email,
            department=data.department,
            role=data.role,
            is_active=data.is_active,
        )
        session.add(employee)
        created.append((employee, data))
    # Insert all identities before setting self-referencing manager foreign keys.
    session.flush()
    for employee, data in created:
        employee.manager_id = data.manager_id
    session.flush()
    return len(created)


def _seed_users(session: Session) -> int:
    created = 0
    for employee_id, role in DEMO_USERS:
        existing = session.scalar(select(User).where(User.employee_id == employee_id))
        if existing is not None:
            continue
        employee = session.get(Employee, employee_id)
        if employee is None:
            raise ValueError(f"Demo employee {employee_id} is missing")
        session.add(
            User(
                id=_demo_id("users", employee_id),
                employee_id=employee_id,
                name=employee.name,
                email=employee.email,
                role=role,
            )
        )
        created += 1
    session.flush()
    return created


def _seed_repositories(session: Session) -> tuple[dict[str, Repository], int]:
    repositories: dict[str, Repository] = {}
    created = 0
    for data in REPOSITORIES:
        repository = session.scalar(select(Repository).where(Repository.name == data.name))
        if repository is None:
            repository = Repository(
                id=_demo_id("repositories", data.name),
                name=data.name,
                owning_department=data.department,
                sensitivity_level=data.sensitivity,
            )
            session.add(repository)
            created += 1
        repositories[data.name] = repository
    session.flush()
    return repositories, created


def _seed_permissions(session: Session, repositories: dict[str, Repository]) -> int:
    created = 0
    for employee_id, name, permission in INITIAL_PERMISSIONS:
        repository = repositories[name]
        existing = session.scalar(
            select(RepositoryPermission).where(
                RepositoryPermission.employee_id == employee_id,
                RepositoryPermission.repository_id == repository.id,
            )
        )
        if existing is not None:
            continue
        session.add(
            RepositoryPermission(
                id=_demo_id("permissions", f"{employee_id}/{name}"),
                employee_id=employee_id,
                repository_id=repository.id,
                permission=permission,
            )
        )
        created += 1
    session.flush()
    return created


def _seed_policies(session: Session, policies: list[PolicySeed]) -> tuple[int, int]:
    created = 0
    updated = 0
    existing = {
        doc.slug: doc
        for doc in session.scalars(
            select(KnowledgeDocument).order_by(KnowledgeDocument.id).with_for_update()
        )
    }
    for data in policies:
        document = existing.get(data.slug)
        if document is None:
            document = KnowledgeDocument(id=_demo_id("policies", data.slug), slug=data.slug)
            session.add(document)
            created += 1
        elif document.content_hash == data.content_hash:
            continue
        else:
            # Changed source text invalidates future RAG chunks and embeddings.
            session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
            updated += 1
        document.title = data.title
        document.version = data.version
        document.content = data.content
        document.content_hash = data.content_hash
        document.metadata_ = data.metadata
    session.flush()
    return created, updated


def seed_database(
    session: Session,
    policy_directory: Path,
    embedding_provider: EmbeddingProvider | None = None,
) -> dict[str, int]:
    """Commit seed changes atomically using a dedicated session; roll back on failure.

    Existing identities, repository settings, permissions, and execution history are
    preserved. Only changed policy source documents are updated. Returned counts
    describe changes in this invocation, so an unchanged reseed returns all zeroes.
    """
    try:
        policies = _read_policies(policy_directory)
        employees_created = _seed_employees(session)
        users_created = _seed_users(session)
        repositories, repositories_created = _seed_repositories(session)
        permissions_created = _seed_permissions(session, repositories)
        policies_created, policies_updated = _seed_policies(session, policies)
        index_counts = {}
        if embedding_provider is not None:
            from app.rag.ingestion import ingest_policies

            index_counts = ingest_policies(session, embedding_provider)
        session.commit()
        return {
            "employees_created": employees_created,
            "users_created": users_created,
            "repositories_created": repositories_created,
            "permissions_created": permissions_created,
            "policies_created": policies_created,
            "policies_updated": policies_updated,
            **{f"index_{key}": value for key, value in index_counts.items()},
        }
    except Exception:
        session.rollback()
        raise


def main() -> int:
    from app.core.config import get_settings
    from app.db.session import SessionLocal
    from app.rag.embeddings import create_embedding_provider

    try:
        with SessionLocal() as session:
            settings = get_settings()
            counts = seed_database(
                session, settings.policy_directory, create_embedding_provider(settings)
            )
    except SQLAlchemyError:
        print(
            "Seed failed: database operation failed. Check connectivity and run migrations.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, RagError) as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "seeded", **counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
