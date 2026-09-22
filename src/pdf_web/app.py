'''FastAPI application construction and process lifecycle.'''

from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, Response

from . import APP_NAME, APP_VERSION
from .api.artifacts import router as artifacts_router
from .api.health import router as health_router
from .api.jobs import router as jobs_router
from .api.runtime import ApiRuntime, configure_runtime
from .assets import serve_asset, serve_index, serve_versioned_asset
from .config import CONFIG_DIR, JOBS_ROOT, MIN_FREE_DISK_BYTES, RETENTION_SWEEP_SECONDS
from .identity import describe_mode
from .infrastructure.persistence import JobStore, load_persisted_jobs, sweep_expired_jobs
from .infrastructure.readiness import cached_health, collect_readiness
from .runner import PipelineRunner

STORE = JobStore()
RUNNER = PipelineRunner(STORE)
ASSET_VERSION_PATH = PathParam(..., pattern=r"^[0-9a-f]{12}$")


def _assert_disk_space() -> None:
    '''Refuse submissions when the job volume is nearly full.'''
    try:
        usage = shutil.disk_usage(JOBS_ROOT)
    except OSError:
        return
    if usage.free < MIN_FREE_DISK_BYTES:
        raise HTTPException(
            status_code=507,
            detail="Not enough free disk space to accept a new job.",
        )


def _new_job_id(taken: set[str]) -> str:
    '''Mint a unique, sortable filesystem-safe identifier.'''
    while True:
        candidate = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
        if candidate in taken or STORE.get(candidate) is not None:
            continue
        if (JOBS_ROOT / candidate).exists():
            continue
        taken.add(candidate)
        return candidate


def _runtime() -> ApiRuntime:
    '''Resolve globals at request time to retain deployment/test injection.'''
    return ApiRuntime(
        store=STORE,
        runner=RUNNER,
        jobs_root=JOBS_ROOT,
        config_dir=CONFIG_DIR,
        new_job_id=_new_job_id,
        assert_disk_space=_assert_disk_space,
        cached_health=cached_health,
        collect_readiness=collect_readiness,
        describe_mode=describe_mode,
    )


configure_runtime(_runtime)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    '''Load persisted jobs, start workers, and sweep expired work on shutdown.'''
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    _loaded, unowned = load_persisted_jobs(STORE)
    if unowned:
        print(
            f"{APP_NAME}: {unowned} job(s) have no recorded owner and are "
            "unreachable by every user. They predate per-user ownership. "
            "Set PDF_WEB_LEGACY_JOB_OWNER to adopt them, or delete "
            f"{JOBS_ROOT} entries you no longer need."
        )
    sweep_expired_jobs(STORE)
    RUNNER.start()
    sweep_task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        sweep_task.cancel()
        await asyncio.gather(sweep_task, return_exceptions=True)
        await asyncio.to_thread(RUNNER.stop)


async def _retention_loop() -> None:
    '''Delete expired job directories on a slow timer.'''
    while True:
        await asyncio.sleep(RETENTION_SWEEP_SECONDS)
        await asyncio.to_thread(sweep_expired_jobs, STORE)


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
app.include_router(health_router)
app.include_router(jobs_router)
app.include_router(artifacts_router)


@app.get("/", response_class=HTMLResponse)
async def index() -> Response:
    '''Serve the browser application shell.'''
    return serve_index()


@app.get("/static/style.css")
async def stylesheet() -> Response:
    return serve_asset("style.css", "text/css")


@app.get("/static/app.js")
async def script() -> Response:
    return serve_asset("app.js", "text/javascript")


@app.get("/static/browser-api.js")
async def browser_api_script() -> Response:
    return serve_asset("browser-api.js", "text/javascript")


@app.get("/static/live-updates.js")
async def live_updates_script() -> Response:
    return serve_asset("live-updates.js", "text/javascript")


@app.get("/static/style.{version}.css")
async def versioned_stylesheet(version: str = ASSET_VERSION_PATH) -> Response:
    return serve_versioned_asset("style.css", version, "text/css")


@app.get("/static/app.{version}.js")
async def versioned_script(version: str = ASSET_VERSION_PATH) -> Response:
    return serve_versioned_asset("app.js", version, "text/javascript")


@app.get("/static/browser-api.{version}.js")
async def versioned_browser_api_script(version: str = ASSET_VERSION_PATH) -> Response:
    return serve_versioned_asset("browser-api.js", version, "text/javascript")


@app.get("/static/live-updates.{version}.js")
async def versioned_live_updates_script(version: str = ASSET_VERSION_PATH) -> Response:
    return serve_versioned_asset("live-updates.js", version, "text/javascript")


@app.get("/static/{module_name}.{version}.js")
async def versioned_frontend_module(
        module_name: str, version: str = ASSET_VERSION_PATH) -> Response:
    if module_name not in {"api", "state", "dom", "upload-staging", "queue-view", "dialogs", "job-detail", "sse"}:
        raise HTTPException(status_code=404, detail="Not found.")
    return serve_versioned_asset(module_name + ".js", version, "text/javascript")


@app.get("/static/{module_name}.js")
async def frontend_module(module_name: str) -> Response:
    if module_name not in {"api", "state", "dom", "upload-staging", "queue-view", "dialogs", "job-detail", "sse"}:
        raise HTTPException(status_code=404, detail="Not found.")
    return serve_asset(module_name + ".js", "text/javascript")
