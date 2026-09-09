import logging
import threading
from collections.abc import Callable
from uuid import UUID, uuid4

from langgraph.types import Command
from langsmith import tracing_context
from sqlalchemy.orm import Session, sessionmaker

from app.agents.checkpoints import checkpoint_store
from app.agents.graph import AccessWorkflow
from app.agents.state import AgentState
from app.core.config import Settings
from app.models import AgentRun, ApprovalRequest, ApprovalStatus
from app.providers import AgentProvider
from app.providers.factory import create_provider
from app.rag.contracts import EmbeddingProvider
from app.services import runs

logger = logging.getLogger("resolveai.worker")


class AgentWorker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        settings: Settings,
        provider: AgentProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        tool_overrides: dict[str, Callable] | None = None,
        evaluate_completed: bool = True,
    ) -> None:
        self.sessions, self.settings, self.provider = sessions, settings, provider
        self.embedding_provider = embedding_provider
        self.tool_overrides = tool_overrides
        self.evaluate_completed = evaluate_completed
        self.owner = str(uuid4())
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def run_once(self) -> UUID | None:
        with self.sessions() as session:
            claimed = runs.claim_run(session, self.owner, self.settings.agent_lease_seconds)
            if claimed is None:
                run_id = None
            else:
                run_id = claimed.id
        if run_id is None:
            if self.evaluate_completed:
                self.evaluate_idle()
            return None
        with self.sessions() as session:
            claimed = session.get(AgentRun, run_id)
            run_id, initial, attempts = claimed.id, claimed.state, claimed.attempt_count
            if initial.get("workflow_version", 1) >= 3:
                attempts = claimed.recovery_attempt_count
            provider_name, model_name = claimed.provider_name, claimed.model_name
        if attempts > self.settings.agent_max_attempts:
            self.fail_exhausted(run_id)
            return run_id
        try:
            configured = self.settings.model_copy(
                update={"llm_provider": provider_name, "openai_model": model_name}
            )
            state = AgentState.model_validate(initial)
            if state.retrieval_config:
                retrieval = state.retrieval_config
                configured = configured.model_copy(
                    update={
                        "embedding_provider": retrieval.provider,
                        "embedding_model": retrieval.model,
                        "embedding_dimensions": retrieval.dimensions,
                        "rag_top_k": retrieval.top_k,
                        "rag_min_score": retrieval.min_score,
                    }
                )
            provider = self.provider or create_provider(configured)
            with checkpoint_store(self.settings) as saver, tracing_context(enabled=False):
                workflow_class = AccessWorkflow
                if state.workflow_version >= 3:
                    from app.agents.access_flow import AccessFlow

                    workflow_class = AccessFlow
                graph = workflow_class(
                    self.sessions,
                    provider,
                    configured,
                    run_id,
                    self.owner,
                    self.embedding_provider,
                    self.tool_overrides,
                ).compile(saver, workflow_version=state.workflow_version)
                config = {
                    "configurable": {"thread_id": initial["graph_thread_id"]},
                    "recursion_limit": 30,
                }
                snapshot = graph.get_state(config)
                if snapshot.values:
                    if any(task.interrupts for task in snapshot.tasks):
                        saved = AgentState.model_validate(snapshot.values)
                        with self.sessions() as session:
                            approval = session.get(ApprovalRequest, saved.approval_id)
                            if approval is not None and approval.status == ApprovalStatus.PENDING:
                                runs.mark_waiting(session, run_id, self.owner, saved)
                                return run_id
                        # This value only wakes the graph. CHECK_APPROVAL reloads the record.
                        result = graph.invoke(Command(resume={"decision_recorded": True}), config)
                    else:
                        result = graph.invoke(None, config) if snapshot.next else snapshot.values
                else:
                    result = graph.invoke(initial, config)
                if result.get("__interrupt__"):
                    paused = AgentState.model_validate(graph.get_state(config).values)
                    with self.sessions() as session:
                        runs.mark_waiting(session, run_id, self.owner, paused)
                    return run_id
                state = AgentState.model_validate(result)
                if state.outcome is None:
                    raise RuntimeError("Graph completed without a terminal outcome.")
                with self.sessions() as session:
                    runs.complete_run(session, run_id, self.owner, state)
                self.evaluate_terminal(run_id)
        except runs.LeaseLost:
            logger.warning("agent_lease_lost")
        except Exception as exc:
            # The lease expires for crash recovery. No generic error is turned into success.
            logger.error("agent_attempt_failed", extra={"error_type": type(exc).__name__})
            if attempts >= self.settings.agent_max_attempts:
                self.fail_exhausted(run_id)
        return run_id

    def fail_exhausted(self, run_id: UUID) -> None:
        with self.sessions() as session:
            runs.fail_exhausted(session, run_id, self.owner, self.settings.agent_lease_seconds)
        self.evaluate_terminal(run_id)

    def evaluate_terminal(self, run_id: UUID) -> None:
        if not self.evaluate_completed:
            return
        try:
            from app.evaluation.service import evaluate_run

            with self.sessions() as session:
                evaluate_run(session, run_id)
        except Exception as exc:
            # Completion is already durable; idle scans retry evaluation independently.
            logger.error("run_evaluation_failed", extra={"error_type": type(exc).__name__})

    def evaluate_idle(self) -> None:
        from app.evaluation.service import backfill_one, process_next

        try:
            backfill_one(self.sessions)
        except Exception as exc:
            logger.error("evaluation_worker_unavailable", extra={"error_type": type(exc).__name__})
        try:
            process_next(self.sessions, self.settings, self.owner)
        except Exception as exc:
            logger.error("evaluation_batch_unavailable", extra={"error_type": type(exc).__name__})

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.error("agent_worker_unavailable", extra={"error_type": type(exc).__name__})
            self.stop_event.wait(self.settings.agent_poll_seconds)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="resolveai-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
