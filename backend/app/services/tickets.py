from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models import (
    AgentRun,
    ConversationMessage,
    ConversationRole,
    Employee,
    Ticket,
    TicketStatus,
)
from app.schemas.tickets import TicketCreate, TicketUpdate


def get_ticket(session: Session, ticket_id: UUID, *, for_update: bool = False) -> Ticket:
    query = select(Ticket).where(Ticket.id == ticket_id)
    if for_update:
        query = query.with_for_update()
    ticket = session.scalar(query)
    if ticket is None:
        raise DomainError(404, "ticket_not_found", "Ticket does not exist.")
    return ticket


def create_ticket(session: Session, payload: TicketCreate) -> Ticket:
    employee = session.get(Employee, payload.employee_id)
    if employee is None:
        raise DomainError(404, "employee_not_found", "Employee does not exist.")
    if not employee.is_active:
        raise DomainError(
            409, "employee_inactive", "Inactive employees cannot submit access requests."
        )
    ticket = Ticket(employee_id=employee.id, request_text=payload.request_text)
    session.add(ticket)
    session.flush()
    session.add(
        ConversationMessage(
            ticket_id=ticket.id, role=ConversationRole.USER, content=payload.request_text
        )
    )
    session.commit()
    session.refresh(ticket)
    return ticket


def list_tickets(
    session: Session,
    *,
    limit: int,
    offset: int,
    status: TicketStatus | None = None,
    employee_id: str | None = None,
) -> tuple[list[Ticket], int]:
    query = select(Ticket)
    if status is not None:
        query = query.where(Ticket.status == status)
    if employee_id is not None:
        query = query.where(Ticket.employee_id == employee_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(
        query.order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(limit).offset(offset)
    ).all()
    return list(items), total


def ensure_editable(session: Session, ticket: Ticket) -> None:
    has_run = session.scalar(select(AgentRun.id).where(AgentRun.ticket_id == ticket.id).limit(1))
    if ticket.status != TicketStatus.OPEN or has_run is not None:
        raise DomainError(409, "ticket_locked", "Only unprocessed open tickets can be changed.")


def update_ticket(session: Session, ticket_id: UUID, payload: TicketUpdate) -> Ticket:
    ticket = get_ticket(session, ticket_id, for_update=True)
    ensure_editable(session, ticket)
    ticket.request_text = payload.request_text
    ticket.updated_at = datetime.now(UTC)
    message = session.scalar(
        select(ConversationMessage)
        .where(
            ConversationMessage.ticket_id == ticket.id,
            ConversationMessage.role == ConversationRole.USER,
        )
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
        .limit(1)
    )
    if message is not None:
        message.content = payload.request_text
    session.commit()
    session.refresh(ticket)
    return ticket


def delete_ticket(session: Session, ticket_id: UUID) -> None:
    ticket = get_ticket(session, ticket_id, for_update=True)
    ensure_editable(session, ticket)
    session.delete(ticket)
    session.commit()
