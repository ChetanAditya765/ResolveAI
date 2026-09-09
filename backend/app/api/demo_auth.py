from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.api.dependencies import DBSession
from app.core.demo_auth import active_user, current_user, issue_session, require_demo_mode
from app.models import Employee, User
from app.schemas.auth import SessionCreate, SessionRead, UserRead

router = APIRouter(tags=["demo sessions"])
CurrentUser = Annotated[User, Depends(current_user)]


@router.get("/demo/users", response_model=list[UserRead])
def demo_users(session: DBSession, request: Request):
    require_demo_mode(request.app.state.settings)
    return list(
        session.scalars(
            select(User).join(Employee).where(Employee.is_active.is_(True)).order_by(User.name)
        )
    )


@router.post("/demo/session", response_model=SessionRead)
def create_demo_session(data: SessionCreate, session: DBSession, request: Request):
    require_demo_mode(request.app.state.settings)
    return issue_session(active_user(session, data.user_id), request.app.state.settings)


@router.get("/session", response_model=UserRead)
def read_session(user: CurrentUser):
    return user
