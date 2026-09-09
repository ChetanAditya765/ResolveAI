from uuid import UUID

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.dependencies import DBSession, PageLimit, PageOffset
from app.models import ConversationMessage, TicketStatus
from app.schemas.tickets import (
    MessageRead,
    Page,
    TicketCreate,
    TicketDetail,
    TicketRead,
    TicketUpdate,
)
from app.services import tickets as service

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.post("", response_model=TicketRead, status_code=201)
def create_ticket(payload: TicketCreate, session: DBSession, response: Response) -> TicketRead:
    ticket = service.create_ticket(session, payload)
    response.headers["Location"] = f"/api/tickets/{ticket.id}"
    return TicketRead.model_validate(ticket)


@router.get("", response_model=Page[TicketRead])
def list_tickets(
    session: DBSession,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
    status: TicketStatus | None = None,
    employee_id: str | None = None,
) -> Page[TicketRead]:
    items, total = service.list_tickets(
        session, limit=limit, offset=offset, status=status, employee_id=employee_id
    )
    return Page(
        items=[TicketRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: UUID, session: DBSession) -> TicketDetail:
    ticket = service.get_ticket(session, ticket_id)
    messages = session.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.ticket_id == ticket_id)
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
    ).all()
    return TicketDetail(
        **TicketRead.model_validate(ticket).model_dump(),
        messages=[MessageRead.model_validate(item) for item in messages],
    )


@router.patch("/{ticket_id}", response_model=TicketRead)
def update_ticket(ticket_id: UUID, payload: TicketUpdate, session: DBSession) -> TicketRead:
    return TicketRead.model_validate(service.update_ticket(session, ticket_id, payload))


@router.delete("/{ticket_id}", status_code=204)
def delete_ticket(ticket_id: UUID, session: DBSession) -> Response:
    service.delete_ticket(session, ticket_id)
    return Response(status_code=204)
