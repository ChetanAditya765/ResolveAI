from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAt, Timestamps, UTCDateTime, UUIDPrimaryKey
from app.models.enums import ConversationRole, TicketStatus, enum_column
from app.models.identity import Employee


class Ticket(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("length(trim(request_text)) > 0", name="request_not_blank"),
        CheckConstraint(
            "status <> 'RESOLVED' OR resolved_at IS NOT NULL", name="resolved_has_timestamp"
        ),
    )

    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        enum_column(TicketStatus, "ticket_status"),
        default=TicketStatus.OPEN,
        server_default=TicketStatus.OPEN.value,
        index=True,
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    final_response: Mapped[str | None] = mapped_column(Text)

    employee: Mapped[Employee] = relationship()
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="ticket",
        order_by="ConversationMessage.created_at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ConversationMessage(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "conversation_messages"

    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[ConversationRole] = mapped_column(
        enum_column(ConversationRole, "conversation_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")
