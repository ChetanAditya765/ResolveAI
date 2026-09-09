from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_session

DBSession = Annotated[Session, Depends(get_session)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]
