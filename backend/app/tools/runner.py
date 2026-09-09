import hashlib
import json
from collections.abc import Callable
from time import perf_counter
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import LeaseLost
from app.db.base import utc_now
from app.models import AgentRun, AgentStep, Ticket, ToolExecution, ToolExecutionStatus
from app.rag.contracts import EmbeddingProvider, PolicySearch, RagError
from app.tools import domain
from app.tools.access_schemas import AccessRequest, ApprovalLookup, CloseRequest, GrantRequest
from app.tools.schemas import (
    EmployeeLookup,
    EscalationInput,
    PermissionLookup,
    RepositoryLookup,
    ToolResult,
)

TOOL_REGISTRY = {
    "get_employee": (EmployeeLookup, domain.get_employee),
    "find_repository": (RepositoryLookup, domain.find_repository),
    "get_repository_permission": (PermissionLookup, domain.get_repository_permission),
    "escalate_ticket": (EscalationInput, domain.escalate_ticket),
    "search_policies": (PolicySearch, None),
    "create_approval_request": (AccessRequest, None),
    "get_approval_status": (ApprovalLookup, None),
    "grant_repository_permission": (GrantRequest, None),
    "close_ticket": (CloseRequest, None),
}


class ToolContextError(RuntimeError):
    pass


class ToolRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        tool_overrides: dict[str, Callable] | None = None,
    ) -> None:
        self.sessions = session_factory
        self.settings = settings or get_settings()
        self.embedding_provider = embedding_provider
        self.tool_overrides = dict(tool_overrides or {})
        if self.tool_overrides.keys() - TOOL_REGISTRY.keys():
            raise ValueError("Tool overrides must use registered tool names.")

    def search_policies(self, session: Session, args: PolicySearch):
        from app.rag.embeddings import create_embedding_provider
        from app.rag.retrieval import search_policies

        provider = self.embedding_provider or create_embedding_provider(self.settings)
        return search_policies(session, args, provider, min_score=self.settings.rag_min_score)

    def execute(
        self,
        *,
        run_id: UUID,
        step_id: UUID,
        tool_name: str,
        arguments: dict,
        idempotency_key: str,
        lease_owner: str | None = None,
    ) -> ToolResult:
        from app.services.runs import owned_run

        fingerprint = hashlib.sha256(
            json.dumps(arguments, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()
        started = perf_counter()
        with self.sessions() as session:
            if lease_owner is not None:
                run = owned_run(session, run_id, lease_owner)
            else:
                run = session.get(AgentRun, run_id)
            step = session.get(AgentStep, step_id)
            if run is None or step is None or step.run_id != run.id:
                raise ToolContextError("Tool execution requires a matching run and step.")
            ticket = session.get(Ticket, run.ticket_id)
            if ticket is None:
                raise ToolContextError("Run ticket is missing.")
            existing = session.scalar(
                select(ToolExecution).where(ToolExecution.idempotency_key == idempotency_key)
            )
            conflict = existing is not None
            if existing and (
                existing.run_id == run_id
                and existing.step_id == step_id
                and existing.tool_name == tool_name
                and (existing.result or {}).get("fingerprint") == fingerprint
            ):
                if existing.status != ToolExecutionStatus.RUNNING:
                    return ToolResult.model_validate(existing.result["tool_result"])
                # A crashed read or rolled-back transaction is safe to retry.
                conflict = False
                execution = existing
            else:
                schema = TOOL_REGISTRY.get(tool_name, (None, None))[0]
                allowed = schema.model_fields if schema else {}
                safe_arguments = {
                    key: value if key in allowed else "[redacted]"
                    for key, value in arguments.items()
                }
                execution = ToolExecution(
                    run_id=run_id,
                    step_id=step_id,
                    tool_name=tool_name[:100],
                    arguments=safe_arguments,
                    status=ToolExecutionStatus.RUNNING,
                    idempotency_key=None if conflict else idempotency_key,
                    result={"fingerprint": fingerprint},
                )
                session.add(execution)
                session.flush()
            execution_id = execution.id
            session.commit()
            try:
                if lease_owner is not None:
                    run = owned_run(session, run_id, lease_owner)
                if conflict:
                    raise domain.ToolError(
                        "idempotency_conflict", "Tool key was reused with changed scope."
                    )
                if tool_name not in TOOL_REGISTRY:
                    raise domain.ToolError(
                        "tool_not_allowed", "The requested tool is not available."
                    )
                schema, handler = TOOL_REGISTRY[tool_name]
                if tool_name == "search_policies":
                    handler = self.search_policies
                elif tool_name in {
                    "create_approval_request",
                    "get_approval_status",
                    "grant_repository_permission",
                    "close_ticket",
                }:
                    from functools import partial

                    from app.tools import access, approvals

                    required_node = {
                        "create_approval_request": "REQUEST_APPROVAL",
                        "grant_repository_permission": "EXECUTE_ACTION",
                        "close_ticket": "RESOLVE",
                    }.get(tool_name)
                    if required_node and (lease_owner is None or step.node != required_node):
                        raise domain.ToolError(
                            "execution_not_allowed",
                            "This action requires its leased workflow step.",
                        )
                    operations = {
                        "create_approval_request": approvals.create_approval_request,
                        "get_approval_status": approvals.get_approval_status,
                        "grant_repository_permission": access.grant_repository_permission,
                        "close_ticket": access.close_ticket,
                    }
                    handler = partial(operations[tool_name], run_id=run_id)
                typed_args = schema.model_validate(arguments)
                domain.validate_scope(ticket, typed_args.model_dump(mode="json"), run.ticket_id)
                handler = self.tool_overrides.get(tool_name, handler)
                output: BaseModel = handler(session, typed_args)
                result = ToolResult(
                    execution_id=execution_id,
                    tool_name=tool_name,
                    succeeded=True,
                    output=output.model_dump(mode="json"),
                    summary="Tool execution succeeded.",
                )
            except (SQLAlchemyError, LeaseLost):
                # A database outage must leave a retryable record, never a claimed success.
                session.rollback()
                raise
            except Exception as exc:
                session.rollback()
                if lease_owner is not None:
                    owned_run(session, run_id, lease_owner)
                code = (
                    exc.code
                    if isinstance(exc, (domain.ToolError, RagError))
                    else (
                        "invalid_arguments" if isinstance(exc, ValidationError) else "tool_failed"
                    )
                )
                message = (
                    str(exc)
                    if isinstance(exc, (domain.ToolError, RagError))
                    else "Tool execution failed safely."
                )
                result = ToolResult(
                    execution_id=execution_id,
                    tool_name=tool_name,
                    succeeded=False,
                    output={},
                    error_code=code,
                    summary=message,
                )
            execution = session.get(ToolExecution, execution_id)
            execution.result = {
                "fingerprint": fingerprint,
                "tool_result": result.model_dump(mode="json"),
            }
            execution.status = (
                ToolExecutionStatus.SUCCEEDED if result.succeeded else ToolExecutionStatus.FAILED
            )
            execution.error = result.error_code
            execution.completed_at = utc_now()
            execution.latency_ms = round((perf_counter() - started) * 1000)
            session.commit()
            return result
