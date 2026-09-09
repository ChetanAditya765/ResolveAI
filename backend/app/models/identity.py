from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey
from app.models.enums import UserRole, enum_column


class Employee(Timestamps, Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint("manager_id IS NULL OR manager_id <> id", name="not_own_manager"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    department: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(120), nullable=False)
    manager_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )

    manager: Mapped["Employee | None"] = relationship(remote_side="Employee.id")


class User(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "users"

    employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"), unique=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole, "user_role"), default=UserRole.EMPLOYEE, nullable=False
    )

    employee: Mapped[Employee | None] = relationship()
