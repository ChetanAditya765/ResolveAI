from enum import StrEnum

from sqlalchemy import Enum


class Permission(StrEnum):
    NONE = "none"
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class UserRole(StrEnum):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    ADMIN = "admin"


class RepositorySensitivity(StrEnum):
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    PROCESSING = "PROCESSING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class AgentRunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AgentStepStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    WAITING = "WAITING"
    FAILED = "FAILED"


class ToolExecutionStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


def enum_column(enum_class: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        values_callable=lambda values: [item.value for item in values],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )
