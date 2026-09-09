from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, or_, select

from app.api.demo_auth import CurrentUser
from app.api.dependencies import DBSession, PageLimit, PageOffset
from app.models import (
    AgentRun,
    AgentRunStatus,
    ApprovalRequest,
    ApprovalStatus,
    Employee,
    Repository,
    User,
    UserRole,
)
from app.schemas.approvals import ApprovalDecision, ApprovalRead
from app.schemas.auth import UserRead
from app.schemas.tickets import Page
from app.services import approvals
from app.tools.access_schemas import ApprovalInfo
from app.tools.schemas import EmployeeInfo, RepositoryInfo

router = APIRouter(tags=["approvals"])


def present(session, approval: ApprovalRequest, actor: User) -> ApprovalRead:
    employee = session.get(Employee, approval.employee_id)
    run = session.get(AgentRun, approval.run_id)
    can_decide = actor.employee_id != employee.id and (
        actor.role == UserRole.ADMIN
        or (
            actor.role == UserRole.MANAGER
            and actor.id == approval.approver_id
            and actor.employee_id == employee.manager_id
        )
    )
    return ApprovalRead(
        **ApprovalInfo.model_validate(approval).model_dump(),
        employee=EmployeeInfo.model_validate(employee),
        repository=RepositoryInfo.model_validate(session.get(Repository, approval.repository_id)),
        approver=UserRead.model_validate(session.get(User, approval.approver_id)),
        can_decide=can_decide
        and approval.status == ApprovalStatus.PENDING
        and run.status == AgentRunStatus.WAITING_FOR_APPROVAL,
    )


@router.get("/approvals", response_model=Page[ApprovalRead])
def list_approvals(
    session: DBSession,
    actor: CurrentUser,
    status: ApprovalStatus | None = None,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
):
    query = select(ApprovalRequest)
    if actor.role != UserRole.ADMIN:
        query = query.where(
            or_(
                ApprovalRequest.approver_id == actor.id,
                ApprovalRequest.employee_id == actor.employee_id,
            )
        )
    if status:
        query = query.where(ApprovalRequest.status == status)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    records = session.scalars(
        query.order_by(ApprovalRequest.requested_at, ApprovalRequest.id).limit(limit).offset(offset)
    )
    return Page(
        items=[present(session, item, actor) for item in records],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/approvals/{approval_id}", response_model=ApprovalRead)
def approval_detail(approval_id: UUID, session: DBSession, actor: CurrentUser):
    return present(session, approvals.get_approval(session, approval_id, actor), actor)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalRead)
def approve(approval_id: UUID, data: ApprovalDecision, session: DBSession, actor: CurrentUser):
    return present(
        session,
        approvals.decide_approval(
            session, approval_id, actor, ApprovalStatus.APPROVED, data.comment
        ),
        actor,
    )


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalRead)
def reject(approval_id: UUID, data: ApprovalDecision, session: DBSession, actor: CurrentUser):
    return present(
        session,
        approvals.decide_approval(
            session, approval_id, actor, ApprovalStatus.REJECTED, data.comment
        ),
        actor,
    )
