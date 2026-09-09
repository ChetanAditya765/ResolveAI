from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.dependencies import DBSession, PageLimit, PageOffset
from app.models import Employee, Repository
from app.schemas.catalog import EmployeeRead, RepositoryRead
from app.schemas.tickets import Page

router = APIRouter(tags=["catalog"])


@router.get("/employees", response_model=Page[EmployeeRead])
def list_employees(
    session: DBSession,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
) -> Page[EmployeeRead]:
    items = session.scalars(select(Employee).order_by(Employee.id).limit(limit).offset(offset))
    total = session.scalar(select(func.count()).select_from(Employee)) or 0
    return Page(
        items=[EmployeeRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/repositories", response_model=Page[RepositoryRead])
def list_repositories(
    session: DBSession,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
) -> Page[RepositoryRead]:
    items = session.scalars(
        select(Repository).order_by(Repository.name).limit(limit).offset(offset)
    )
    total = session.scalar(select(func.count()).select_from(Repository)) or 0
    return Page(
        items=[RepositoryRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
