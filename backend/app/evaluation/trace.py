from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evaluation.contracts import EvaluationTrace
from app.models import AgentRun, AgentStep, ApprovalRequest, Ticket, ToolExecution, User
from app.schemas.runs import RunRead, StepRead, ToolExecutionRead
from app.schemas.tickets import TicketRead
from app.tools.access_schemas import ApprovalInfo


def collect_trace(
    session: Session,
    run_id: UUID,
    *,
    initial_permission: str | None = None,
    final_permission: str | None = None,
) -> EvaluationTrace:
    run = session.get(AgentRun, run_id)
    ticket = session.get(Ticket, run.ticket_id)
    steps = session.scalars(
        select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.sequence)
    )
    tools = session.scalars(
        select(ToolExecution)
        .where(ToolExecution.run_id == run_id)
        .order_by(ToolExecution.started_at, ToolExecution.id)
    )
    approvals = session.scalars(select(ApprovalRequest).where(ApprovalRequest.run_id == run_id))
    requester = session.scalar(select(User).where(User.employee_id == ticket.employee_id))
    return EvaluationTrace(
        run=RunRead.model_validate(run).model_dump(mode="json"),
        ticket=TicketRead.model_validate(ticket).model_dump(mode="json"),
        steps=[StepRead.model_validate(row).model_dump(mode="json") for row in steps],
        tools=[ToolExecutionRead.model_validate(row).model_dump(mode="json") for row in tools],
        approvals=[
            {
                **ApprovalInfo.model_validate(row).model_dump(mode="json"),
                "requester_user_id": str(requester.id) if requester else None,
            }
            for row in approvals
        ],
        initial_permission=initial_permission,
        final_permission=final_permission,
    )
