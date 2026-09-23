'''FastAPI application factory and process lifecycle.'''

from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, Response

from . import APP_NAME, APP_VERSION
from .api.artifacts import router as artifacts_router
from .api.health import router as health_router
from .api.jobs import router as jobs_router
from .api.runtime import ApiRuntime
from .assets import serve_asset, serve_index, serve_versioned_asset
from .config import CONFIG_DIR, JOBS_ROOT, MIN_FREE_DISK_BYTES, RETENTION_SWEEP_SECONDS
from .identity import describe_mode
from .infrastructure.persistence import load_persisted_jobs, sweep_expired_jobs
from .infrastructure.readiness import cached_health, collect_readiness
from .runner import PipelineRunner
from .store import JobStore

ASSET_VERSION_PATH = PathParam(..., pattern=r"^[0-9a-f]{12}$")
FRONTEND_MODULES = {
    "api", "state", "dom", "upload-staging", "queue-view", "dialogs",
    "job-detail", "actions", "sse",
}


def _assert_disk_space(jobs_root: Path) -> None:
    '''Refuse submissions when the job volume is nearly full.'''
    try:
        usage = shutil.disk_usage(jobs_root)
    except OSError:
        return
    if usage.free < MIN_FREE_DISK_BYTES:
        raise HTTPException(
            status_code=507,
            detail="Not enough free disk space to accept a new job.",
        )


def _new_job_id(taken: set[str], store: JobStore, jobs_root: Path) -> str:
    '''Mint a unique, sortable filesystem-safe identifier.'''
    while True:
        candidate = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
        if candidate in taken or store.get(candidate) is not None:
            continue
        if (jobs_root / candidate).exists():
            continue
        taken.add(candidate)
        return candidate


def create_app(
        # pylint: disable=too-many-arguments
        *,
        store: JobStore | None = None,
        runner: PipelineRunner | None = None,
        jobs_root: Path = JOBS_ROOT,
        config_dir: Path = CONFIG_DIR,
        disk_space_check: Callable[[], None] | None = None,
        health_provider: Callable[[], dict[str, Any]] = cached_health,
        readiness_provider: Callable[[], dict[str, Any]] = collect_readiness,
        identity_mode_provider: Callable[[], dict[str, Any]] = describe_mode,
) -> FastAPI:
    '''Build an independent web application with injectable runtime services.'''
    if runner is not None and store is None:
        raise ValueError("Pass the JobStore that owns an injected runner.")
    job_store = store if store is not None else JobStore()
    pipeline_runner = runner if runner is not None else PipelineRunner(job_store)
    runtime = ApiRuntime(
        store=job_store,
        runner=pipeline_runner,
        jobs_root=jobs_root,
        config_dir=config_dir,
        new_job_id=lambda taken: _new_job_id(taken, job_store, jobs_root),
        assert_disk_space=(disk_space_check if disk_space_check is not None else
                           lambda: _assert_disk_space(jobs_root)),
        cached_health=health_provider,
        collect_readiness=readiness_provider,
        describe_mode=identity_mode_provider,
    )

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        '''Recover persisted work, run workers, and sweep expired jobs.'''
        jobs_root.mkdir(parents=True, exist_ok=True)
        _loaded, unowned = load_persisted_jobs(job_store, jobs_root)
        if unowned:
            print(
                f"{APP_NAME}: {unowned} job(s) have no recorded owner and are "
                "unreachable by every user. They predate per-user ownership. "
                "Set PDF_WEB_LEGACY_JOB_OWNER to adopt them, or delete "
                f"{jobs_root} entries you no longer need."
            )
        sweep_expired_jobs(job_store, jobs_root)
        pipeline_runner.start()
        sweep_task = asyncio.create_task(_retention_loop(job_store, jobs_root))
        try:
            yield
        finally:
            sweep_task.cancel()
            await asyncio.gather(sweep_task, return_exceptions=True)
            await asyncio.to_thread(pipeline_runner.stop)

    application = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        lifespan=lifespan,
    )
    application.state.runtime = runtime
    application.include_router(health_router)
    application.include_router(jobs_router)
    application.include_router(artifacts_router)
    _install_asset_routes(application)
    return application


async def _retention_loop(store: JobStore, jobs_root: Path) -> None:
    '''Delete expired job directories on a slow timer.'''
    while True:
        await asyncio.sleep(RETENTION_SWEEP_SECONDS)
        await asyncio.to_thread(sweep_expired_jobs, store, jobs_root)


def _install_asset_routes(application: FastAPI) -> None:
    '''Register the browser shell and versioned static assets.'''
    @application.get("/", response_class=HTMLResponse)
    async def index() -> Response:
        return serve_index()

    @application.get("/static/style.css")
    async def stylesheet() -> Response:
        return serve_asset("style.css", "text/css")

    @application.get("/static/app.js")
    async def script() -> Response:
        return serve_asset("app.js", "text/javascript")

    @application.get("/static/browser-api.js")
    async def browser_api_script() -> Response:
        return serve_asset("browser-api.js", "text/javascript")

    @application.get("/static/live-updates.js")
    async def live_updates_script() -> Response:
        return serve_asset("live-updates.js", "text/javascript")

    @application.get("/static/style.{version}.css")
    async def versioned_stylesheet(version: str = ASSET_VERSION_PATH) -> Response:
        return serve_versioned_asset("style.css", version, "text/css")

    @application.get("/static/app.{version}.js")
    async def versioned_script(version: str = ASSET_VERSION_PATH) -> Response:
        return serve_versioned_asset("app.js", version, "text/javascript")

    @application.get("/static/browser-api.{version}.js")
    async def versioned_browser_api_script(
            version: str = ASSET_VERSION_PATH) -> Response:
        return serve_versioned_asset("browser-api.js", version, "text/javascript")

    @application.get("/static/live-updates.{version}.js")
    async def versioned_live_updates_script(
            version: str = ASSET_VERSION_PATH) -> Response:
        return serve_versioned_asset("live-updates.js", version, "text/javascript")

    @application.get("/static/{module_name}.{version}.js")
    async def versioned_frontend_module(
            module_name: str, version: str = ASSET_VERSION_PATH) -> Response:
        if module_name not in FRONTEND_MODULES:
            raise HTTPException(status_code=404, detail="Not found.")
        return serve_versioned_asset(module_name + ".js", version, "text/javascript")

    @application.get("/static/{module_name}.js")
    async def frontend_module(module_name: str) -> Response:
        if module_name not in FRONTEND_MODULES:
            raise HTTPException(status_code=404, detail="Not found.")
        return serve_asset(module_name + ".js", "text/javascript")


app = create_app()
