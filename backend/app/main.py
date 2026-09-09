import logging
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException

from app import __version__
from app.agents.worker import AgentWorker
from app.api import approvals, catalog, demo_auth, evaluations, health, policies, runs, tickets
from app.core.config import Settings, get_settings
from app.core.errors import DomainError
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.schemas.common import ErrorResponse

logger = logging.getLogger("resolveai.api")


def error_response(status: int, code: str, message: str, details=None) -> JSONResponse:
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error})


def create_app(settings: Settings | None = None, session_factory=None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        worker = AgentWorker(session_factory or SessionLocal, settings)
        application.state.worker = worker
        if settings.agent_worker_enabled:
            worker.start()
        try:
            yield
        finally:
            worker.stop()

    application = FastAPI(
        title="ResolveAI API",
        version=__version__,
        description="Explicit IT access workflows with persistent run and tool traces.",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
        expose_headers=["X-Request-ID", "Location"],
    )

    @application.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id
        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return response

    @application.exception_handler(DomainError)
    async def domain_error(_request: Request, exc: DomainError):
        return error_response(exc.status_code, exc.code, exc.message)

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        details = [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in exc.errors()
        ]
        return error_response(422, "validation_error", "Request validation failed.", details)

    @application.exception_handler(IntegrityError)
    async def integrity_error(_request: Request, _exc: IntegrityError):
        return error_response(409, "data_conflict", "The operation conflicts with existing data.")

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError):
        logger.error(
            "database_operation_failed",
            extra={
                "request_id": getattr(request.state, "request_id", "unknown"),
                "error_type": type(exc).__name__,
            },
        )
        return error_response(
            503, "database_unavailable", "Database operation unavailable. Retry later."
        )

    @application.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        return error_response(exc.status_code, "http_error", str(exc.detail))

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logger.error(
            "unexpected_error",
            extra={
                "request_id": getattr(request.state, "request_id", "unknown"),
                "error_type": type(exc).__name__,
            },
        )
        response = error_response(500, "internal_error", "An unexpected error occurred.")
        response.headers["X-Request-ID"] = getattr(request.state, "request_id", "unknown")
        return response

    errors = {code: {"model": ErrorResponse} for code in (404, 409, 422, 503)}
    for router in (
        health.router,
        catalog.router,
        tickets.router,
        runs.router,
        policies.router,
        demo_auth.router,
        approvals.router,
        evaluations.router,
    ):
        application.include_router(router, prefix="/api", responses=errors)
    return application


app = create_app()
