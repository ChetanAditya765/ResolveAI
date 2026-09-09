from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import Permission, RepositorySensitivity, enum_column
from app.models.identity import Employee


class Repository(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "repositories"

    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    owning_department: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    sensitivity_level: Mapped[RepositorySensitivity] = mapped_column(
        enum_column(RepositorySensitivity, "repository_sensitivity"), nullable=False
    )


class RepositoryPermission(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "repository_permissions"
    __table_args__ = (
        UniqueConstraint("employee_id", "repository_id", name="uq_permission_employee_repository"),
    )

    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    permission: Mapped[Permission] = mapped_column(
        enum_column(Permission, "permission_level"), nullable=False
    )

    employee: Mapped[Employee] = relationship()
    repository: Mapped[Repository] = relationship()
