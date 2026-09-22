'''Health, deployment readiness, identity diagnostics, and config routes.'''

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import APP_VERSION
from ..config import (
    ALLOWED_CONFIG_FILES,
    CONFIG_FILE_DETAILS,
    DEFAULT_CONFIG_FILE,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_SUBMISSION_BYTES,
)
from ..identity import (
    describe_mode,
    diagnose_request,
    header_diagnostic_enabled,
    resolve_user,
)
from .runtime import get_runtime

router = APIRouter()


async def current_user(request: Request) -> str:
    '''Resolve the authenticated user from the trusted proxy.'''
    return resolve_user(request)


CURRENT_USER = Depends(current_user)


@router.get("/healthz")
async def liveness() -> JSONResponse:
    '''Report whether the process and worker pool are alive.'''
    runner = get_runtime().runner
    worker_alive = runner.is_running()
    return JSONResponse(
        status_code=200 if worker_alive else 503,
        content={
            "status": "ok" if worker_alive else "degraded",
            "worker": "running" if worker_alive else "stopped",
            "version": APP_VERSION,
        },
    )


@router.get("/readyz")
async def readiness() -> JSONResponse:
    '''Report deployment readiness without exposing dependency details.'''
    result = await asyncio.to_thread(get_runtime().collect_readiness)
    ready = bool(result["ready"])
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not-ready", "version": APP_VERSION},
    )


@router.get("/api/proxy-headers")
async def proxy_headers(request: Request) -> dict[str, Any]:
    '''Return redacted proxy diagnostics only when explicitly enabled.'''
    if not header_diagnostic_enabled():
        raise HTTPException(status_code=404, detail="Not found.")
    return {"auth": describe_mode(), **diagnose_request(request)}


@router.get("/api/health")
async def health(user: str = CURRENT_USER) -> dict[str, Any]:
    '''Report the tools and deployment readiness needed by the pipeline.'''
    runtime = get_runtime()
    payload, readiness_payload = await asyncio.gather(
        asyncio.to_thread(runtime.cached_health),
        asyncio.to_thread(runtime.collect_readiness),
    )
    return {
        **payload,
        "deployment_readiness": readiness_payload,
        "queue_depth": runtime.runner.queue_depth(),
        "user": user,
        "auth": runtime.describe_mode(),
    }


@router.get("/api/config-files", dependencies=[Depends(current_user)])
async def config_files() -> dict[str, Any]:
    '''List remediation presets and upload limits offered by the portal.'''
    config_dir = get_runtime().config_dir
    return {
        "default": DEFAULT_CONFIG_FILE,
        "upload_limits": {
            "max_files": MAX_FILES,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_submission_bytes": MAX_SUBMISSION_BYTES,
        },
        "files": [
            {
                "name": name,
                **CONFIG_FILE_DETAILS[name],
                "available": (config_dir / name).is_file(),
            }
            for name in ALLOWED_CONFIG_FILES
        ],
    }
