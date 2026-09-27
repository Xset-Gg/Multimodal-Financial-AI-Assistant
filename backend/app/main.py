from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.util import find_spec

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.routes import chat, session, statements, transactions
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.security import CSRF_HEADER
from app.db.session import get_engine

log = logging.getLogger(__name__)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = None
    if settings.embedded_worker:
        from app.jobs.worker import Worker

        worker = Worker()
        worker.start_in_thread()
    log.info("SpendLens API started (groq %s)", "enabled" if settings.groq_enabled else "disabled")
    yield
    if worker:
        worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SpendLens AI API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.environment != "production" else None,
        openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["content-type", CSRF_HEADER, "last-event-id"],
    )

    @app.middleware("http")
    async def security(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # CSRF defence: state-changing API calls must carry a custom header, which a
        # cross-site form or image tag cannot set.
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/") \
                and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": "Missing client header"}, status_code=403)
        # Reject oversize bodies before reading them.
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_upload_bytes + 1024 * 1024:
            return JSONResponse({"detail": f"File is larger than the {settings.max_upload_mb} MB limit."},
                                status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    def health() -> dict[str, object]:
        db_ok = True
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            db_ok = False
        return {
            "status": "ok" if db_ok else "degraded",
            "database": db_ok,
            "docling_installed": find_spec("docling") is not None,
            "vision_enabled": settings.groq_enabled,
            "llm_enabled": settings.groq_enabled,
        }

    app.include_router(session.router)
    app.include_router(statements.router)
    app.include_router(transactions.router)
    app.include_router(chat.router)
    return app


app = create_app()
