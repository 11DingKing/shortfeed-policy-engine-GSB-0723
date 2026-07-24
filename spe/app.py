"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.routes.admin import router as admin_router
from .api.routes.policies import router as policies_router
from .api.routes.sessions import router as sessions_router
from .config import Settings
from .container import AppContainer, build_container


def create_app(settings: Settings | None = None, *, container: AppContainer | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = container or build_container(settings)
        app.state.container = c
        await c.startup()
        try:
            yield
        finally:
            await c.shutdown()

    app = FastAPI(
        title="Shortfeed Policy Engine",
        version="0.1.0",
        description=(
            "Multi-tenant short-video session policy engine with versioned JSON "
            "AST policies, deterministic traces, and transactional outbox."
        ),
        lifespan=lifespan,
    )

    prefix = settings.api_prefix
    app.include_router(admin_router, prefix=prefix)
    app.include_router(policies_router, prefix=prefix)
    app.include_router(sessions_router, prefix=prefix)

    return app


# The default module-level app used by ``uvicorn spe.main:app``.
app = create_app()
