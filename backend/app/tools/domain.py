from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import (
    ConversationMessage,
    ConversationRole,
    Employee,
    Permission,
    Repository,
    RepositoryPermission,
    Ticket,
    TicketStatus,
)
from app.tools.schemas import (
    EmployeeInfo,
    EmployeeLookup,
    EscalationInput,
    EscalationResult,
    PermissionInfo,
    PermissionLookup,
    RepositoryInfo,
    RepositoryLookup,
)


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def get_employee(session: Session, args: EmployeeLookup) -> EmployeeInfo:
    employee = session.get(Employee, args.employee_id)
    if employee is None:
        raise ToolError("employee_not_found", "Employee does not exist.")
    return EmployeeInfo.model_validate(employee)


def find_repository(session: Session, args: RepositoryLookup) -> RepositoryInfo:
    repositories = session.scalars(
        select(Repository).where(func.lower(Repository.name) == args.repository_name.lower())
    ).all()
    if len(repositories) != 1:
        raise ToolError("repository_not_found", "A unique matching repository was not found.")
    return RepositoryInfo.model_validate(repositories[0])


def get_repository_permission(session: Session, args: PermissionLookup) -> PermissionInfo:
    get_employee(session, EmployeeLookup(employee_id=args.employee_id))
    if session.get(Repository, args.repository_id) is None:
        raise ToolError("repository_not_found", "Repository does not exist.")
    record = session.scalar(
        select(RepositoryPermission).where(
            RepositoryPermission.employee_id == args.employee_id,
            RepositoryPermission.repository_id == args.repository_id,
        )
    )
    return PermissionInfo(
        employee_id=args.employee_id,
        repository_id=args.repository_id,
        permission=record.permission if record else Permission.NONE,
    )


def escalate_ticket(session: Session, args: EscalationInput) -> EscalationResult:
    ticket = session.scalar(select(Ticket).where(Ticket.id == args.ticket_id).with_for_update())
    if ticket is None:
        raise ToolError("ticket_not_found", "Ticket does not exist.")
    if ticket.status == TicketStatus.RESOLVED:
        raise ToolError("ticket_locked", "Resolved tickets cannot be escalated by this run.")
    ticket.status = TicketStatus.ESCALATED
    ticket.final_response = args.reason
    ticket.updated_at = utc_now()
    session.add(
        ConversationMessage(
            ticket_id=ticket.id, role=ConversationRole.ASSISTANT, content=args.reason
        )
    )
    return EscalationResult(ticket_id=ticket.id, final_response=args.reason)


def validate_scope(ticket: Ticket, arguments: dict, expected_ticket_id: UUID) -> None:
    if "employee_id" in arguments and arguments["employee_id"] != ticket.employee_id:
        raise ToolError("scope_mismatch", "The tool employee must match the ticket employee.")
    if "ticket_id" in arguments and str(arguments["ticket_id"]) != str(expected_ticket_id):
        raise ToolError("scope_mismatch", "The tool ticket must match the active run.")
