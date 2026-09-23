'''Owner-scoped downloads for uploaded PDFs, reports, logs, and bundles.'''

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from ..identity import resolve_user
from ..services.artifact_paths import ArtifactPathService
from ..services.artifact_service import JobArtifactService
from ..services.job_service import JobAccessService
from .runtime import ApiRuntime, get_runtime

router = APIRouter()
JOB_ID_PATH = PathParam(..., pattern=r"^\d{8}-\d{6}-[0-9a-f]{6}$")
ARTIFACT_PATH = PathParam(..., pattern=r"^(pdf|before|after)$")


async def current_user(request: Request) -> str:
    '''Resolve the authenticated identity for artifact routes.'''
    return resolve_user(request)


CURRENT_USER = Depends(current_user)
CURRENT_RUNTIME = Depends(get_runtime)


def _services(runtime: ApiRuntime):
    access = JobAccessService(runtime.store)
    paths = ArtifactPathService(runtime.jobs_root)
    artifacts = JobArtifactService(runtime.store, paths)
    return access, artifacts, paths


@router.get("/api/jobs/{job_id}/log")
async def job_log(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> FileResponse:
    '''Download the captured pipeline log.'''
    access, _artifacts, paths = _services(runtime)
    job = access.require_access(job_id, user)
    path = paths.require_file(job.log_path)
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        filename=f"{job.job_id}-pipeline.log",
    )


@router.get("/api/jobs/{job_id}/download")
async def download_bundle(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> FileResponse:
    '''Download every artifact for a terminal job as a ZIP archive.'''
    access, artifacts, _paths = _services(runtime)
    job = access.require_access(job_id, user)
    if not job.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")
    path = await artifacts.bundle(job)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{job.job_id}-remediation.zip",
    )


@router.get("/api/jobs/{job_id}/original")
async def open_original(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> FileResponse:
    '''Open the original uploaded PDF inline.'''
    access, _artifacts, paths = _services(runtime)
    job = access.require_access(job_id, user)
    path = paths.require_file(job.input_path)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/api/jobs/{job_id}/{artifact}")
async def download_artifact(
        job_id: str = JOB_ID_PATH,
        artifact: str = ARTIFACT_PATH,
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> FileResponse:
    '''Download the processed PDF or a validation report.'''
    access, _artifacts, paths = _services(runtime)
    job = access.require_access(job_id, user)
    path = job.artifact(artifact)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No {artifact} for this job.")
    path = paths.require_file(path)
    if artifact == "pdf":
        return FileResponse(path, media_type="application/pdf", filename=job.original_name)
    stem = Path(job.original_name).stem
    return FileResponse(
        path, media_type="application/json", filename=f"{stem}-{artifact}.json"
    )
