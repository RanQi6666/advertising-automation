import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.v1.router import api_router
from backend.app.core.config import get_settings
from backend.app.core.errors import AppError, NotFoundError, ProviderError
from backend.app.core.logging import configure_logging
from backend.app.db.init_db import create_all_tables
from backend.app.services.ad_generation_service import (
    recover_ad_generation_jobs_on_startup,
    run_ad_generation_job_recovery_loop,
)
from backend.app.services.generation_task_service import (
    recover_generation_tasks_on_startup,
    run_generation_task_recovery_loop,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    if settings.create_db_on_startup:
        await create_all_tables()
    recovery_loops: list[asyncio.Task[None]] = []
    if (
        settings.generation_task_recovery_enabled
        and settings.environment != "local"
        and not app.dependency_overrides
    ):
        await recover_generation_tasks_on_startup()
        await recover_ad_generation_jobs_on_startup()
        recovery_loops = [
            asyncio.create_task(run_generation_task_recovery_loop()),
            asyncio.create_task(run_ad_generation_job_recovery_loop()),
        ]
    try:
        yield
    finally:
        for recovery_loop in recovery_loops:
            recovery_loop.cancel()
            with suppress(asyncio.CancelledError):
                await recovery_loop


def create_app() -> FastAPI:
    settings = get_settings()
    storage_root = Path(settings.local_storage_root)
    storage_root.mkdir(parents=True, exist_ok=True)

    app = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    app.mount("/storage", StaticFiles(directory=storage_root), name="storage")

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ProviderError)
    async def provider_error_handler(_: Request, exc: ProviderError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = create_app()
