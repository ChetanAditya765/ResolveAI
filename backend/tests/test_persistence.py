from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.seed import seed_database
from app.models import (
    AgentRun,
    AgentRunStatus,
    Employee,
    KnowledgeDocument,
    Permission,
    Repository,
    RepositoryPermission,
    Ticket,
    TicketStatus,
    User,
)


def count_rows(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_seed_contains_complete_demo_data(db_session: Session) -> None:
    assert count_rows(db_session, Employee) >= 10
    assert count_rows(db_session, Repository) >= 5
    assert count_rows(db_session, KnowledgeDocument) >= 5
    assert count_rows(db_session, User) >= 3
    assert count_rows(db_session, RepositoryPermission) >= 3

    employee = db_session.get(Employee, "EMP001")
    assert employee is not None
    assert employee.name.startswith("Chetan")
    assert employee.department == "Engineering"
    assert employee.manager_id
    assert db_session.get(Employee, employee.manager_id) is not None
    assert db_session.scalar(select(Repository).where(Repository.name == "payments")) is not None

    documents = db_session.scalars(select(KnowledgeDocument)).all()
    assert any(document.title == "Repository Access Policy" for document in documents)
    assert all(
        document.version and document.content and document.content_hash for document in documents
    )


def test_seed_is_idempotent_and_preserves_permission_changes(
    db_session: Session, policy_directory: Path
) -> None:
    tables = [Employee, User, Repository, RepositoryPermission, KnowledgeDocument, Ticket]
    before = {model.__tablename__: count_rows(db_session, model) for model in tables}
    permission = db_session.scalar(select(RepositoryPermission).limit(1))
    assert permission is not None
    permission.permission = (
        Permission.WRITE if permission.permission != Permission.WRITE else Permission.READ
    )
    permission_id = permission.id
    expected = permission.permission
    db_session.commit()

    seed_database(db_session, policy_directory)
    db_session.expire_all()

    assert {model.__tablename__: count_rows(db_session, model) for model in tables} == before
    preserved = db_session.get(RepositoryPermission, permission_id)
    assert preserved is not None
    assert preserved.permission == expected


def test_permission_pair_cannot_be_duplicated(db_session: Session) -> None:
    permission = db_session.scalar(select(RepositoryPermission).limit(1))
    assert permission is not None
    db_session.add(
        RepositoryPermission(
            employee_id=permission.employee_id,
            repository_id=permission.repository_id,
            permission=Permission.WRITE,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_ticket_employee_foreign_key_is_enforced(db_session: Session) -> None:
    db_session.add(Ticket(employee_id="UNKNOWN", request_text="Read access to payments"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_employee_cannot_manage_themselves(db_session: Session) -> None:
    employee = db_session.get(Employee, "EMP001")
    assert employee is not None
    employee.manager_id = employee.id
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_resolved_ticket_requires_resolution_timestamp(db_session: Session) -> None:
    db_session.add(
        Ticket(
            employee_id="EMP001",
            request_text="Read access to payments",
            status=TicketStatus.RESOLVED,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_database_rejects_blank_request(db_session: Session) -> None:
    db_session.add(Ticket(employee_id="EMP001", request_text="   "))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_ticket_allows_only_one_active_run_but_retains_history(db_session: Session) -> None:
    ticket = Ticket(employee_id="EMP001", request_text="Write access to payments")
    db_session.add(ticket)
    db_session.flush()
    original_run = AgentRun(ticket_id=ticket.id, status=AgentRunStatus.RUNNING)
    db_session.add(original_run)
    db_session.commit()
    ticket_id = ticket.id
    db_session.add(AgentRun(ticket_id=ticket_id, status=AgentRunStatus.WAITING_FOR_APPROVAL))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    original_run.status = AgentRunStatus.COMPLETED
    db_session.commit()
    db_session.add(AgentRun(ticket_id=ticket_id, status=AgentRunStatus.PENDING))
    db_session.commit()
    assert count_rows(db_session, AgentRun) == 2
