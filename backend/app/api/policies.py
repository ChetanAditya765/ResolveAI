from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.dependencies import DBSession, PageLimit, PageOffset
from app.core.errors import DomainError
from app.models import KnowledgeDocument
from app.rag.contracts import PolicySearch, PolicySearchResult, RagError
from app.schemas.policies import PolicyDocumentDetail, PolicyDocumentRead
from app.schemas.tickets import Page

router = APIRouter(tags=["policies"])


@router.post("/policies/search", response_model=PolicySearchResult)
def search_policy_documents(
    data: PolicySearch, session: DBSession, request: Request
) -> PolicySearchResult:
    from app.rag.embeddings import create_embedding_provider
    from app.rag.retrieval import search_policies

    settings = request.app.state.settings
    try:
        return search_policies(
            session,
            data,
            create_embedding_provider(settings),
            min_score=settings.rag_min_score,
        )
    except RagError as exc:
        raise DomainError(503, exc.code, str(exc)) from None


@router.get("/policies", response_model=Page[PolicyDocumentRead])
def list_policies(
    session: DBSession, limit: PageLimit = 20, offset: PageOffset = 0
) -> Page[PolicyDocumentRead]:
    items = session.scalars(
        select(KnowledgeDocument).order_by(KnowledgeDocument.slug).limit(limit).offset(offset)
    )
    return Page(
        items=[PolicyDocumentRead.model_validate(item) for item in items],
        total=session.scalar(select(func.count()).select_from(KnowledgeDocument)) or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/policies/{document_id}", response_model=PolicyDocumentDetail)
def get_policy(document_id: UUID, session: DBSession) -> PolicyDocumentDetail:
    document = session.get(KnowledgeDocument, document_id)
    if document is None:
        raise DomainError(404, "policy_not_found", "Policy document does not exist.")
    return PolicyDocumentDetail.model_validate(document)
