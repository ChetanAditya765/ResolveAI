from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PolicyDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    slug: str
    title: str
    version: str
    content_hash: str
    updated_at: datetime


class PolicyDocumentDetail(PolicyDocumentRead):
    content: str
