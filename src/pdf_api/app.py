'''
A small asynchronous HTTP surface over the single-PDF pipeline.

Accepts one PDF, runs the same sequence go.py runs, and offers the remediated
PDF plus the before and after validation reports for download. The pipeline
takes minutes, so submission and collection are separate requests.
'''

from __future__ import annotations

import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse

from . import APP_NAME, APP_VERSION
from .capabilities import cached_probe
from .models import DEFAULT_TARGETS, PipelineOptions
from .service import JobRegistry, max_concurrent_jobs

ALLOWED_CONFIG_FILES = ("default.json", "default-slim.json")
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

JOB_ID_PATH = PathParam(..., pattern=r"^[0-9a-f]{32}$")
ARTIFACT_PATH = PathParam(..., pattern=r"^(pdf|before|after)$")

REGISTRY: JobRegistry | None = None
_WORKSPACE: Path | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    '''
    Create the job registry and clean it up on shutdown.
    '''
    global REGISTRY, _WORKSPACE  # pylint: disable=global-statement
    _WORKSPACE = Path(tempfile.mkdtemp(prefix="pdf-api-jobs-"))
    REGISTRY = JobRegistry(_WORKSPACE)
    try:
        yield
    finally:
        REGISTRY.shutdown()
        shutil.rmtree(_WORKSPACE, ignore_errors=True)


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)


def registry() -> JobRegistry:
    '''
    Return the running registry, or fail loudly if the app is not started.
    '''
    if REGISTRY is None:
        raise HTTPException(status_code=503, detail="Service is starting.")
    return REGISTRY


@app.get("/healthz")
async def liveness() -> JSONResponse:
    '''
    Report whether the service can do useful work.

    Validation is the hard requirement: without Java and the veraPDF jar every
    PDF would be reported as unvalidatable while the service still looked fine.
    '''
    probe = cached_probe()
    healthy = probe.can_validate()
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "version": APP_VERSION,
            "can_validate": probe.can_validate(),
            "font_fix_available": probe.can_font_fix_callas(),
            "concurrency": max_concurrent_jobs(),
        },
    )


@app.get("/api/capabilities")
async def describe_capabilities() -> dict[str, Any]:
    '''
    Describe the tooling available to the pipeline.
    '''
    probe = cached_probe()
    return {
        "can_validate": probe.can_validate(),
        "callas_font_fix": probe.can_font_fix_callas(),
        "pdfix_font_fix": probe.can_font_fix_pdfix(),
        "detail": probe.detail,
        "config_files": list(ALLOWED_CONFIG_FILES),
    }


@app.post("/api/pdf", status_code=202)
async def submit_pdf(
        file: UploadFile = File(...),
        config_file: str = Form("default.json"),
        wcag_and_ua1_must_pass: bool = Form(False),
        attempt_font_fix: bool = Form(True),
        attempt_unlock: bool = Form(True)) -> JSONResponse:
    '''
    Accept one PDF and queue it for remediation.
    '''
    if config_file not in ALLOWED_CONFIG_FILES:
        raise HTTPException(
            status_code=400, detail=f"Unknown configuration: {config_file}"
        )
    original_name = Path(file.filename or "upload.pdf").name
    if not original_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf uploads are accepted.")

    active = registry()
    upload_dir = Path(tempfile.mkdtemp(prefix="pdf-api-upload-"))
    stored_path = upload_dir / original_name
    try:
        _stream_upload(file, stored_path)
    except ValueError as error:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(status_code=413, detail=str(error)) from error

    if stored_path.read_bytes()[:5] != b"%PDF-":
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="File is not a PDF.")

    job = active.submit(
        original_name,
        stored_path,
        PipelineOptions(
            config_file=config_file,
            wcag_and_ua1_must_pass=wcag_and_ua1_must_pass,
            attempt_font_fix=attempt_font_fix,
            attempt_unlock=attempt_unlock,
            targets=DEFAULT_TARGETS,
        ),
    )
    return JSONResponse(status_code=202, content=job.to_dict())


@app.get("/api/pdf")
async def list_jobs() -> dict[str, Any]:
    '''
    List submitted jobs, newest first.
    '''
    return {"jobs": [job.to_dict() for job in registry().list_jobs()]}


@app.get("/api/pdf/{job_id}")
async def get_job(job_id: str = JOB_ID_PATH) -> dict[str, Any]:
    '''
    Report a job's progress and, once finished, both validation reports.
    '''
    return _require_job(job_id).to_dict()


@app.get("/api/pdf/{job_id}/{artifact}")
async def download(
        job_id: str = JOB_ID_PATH,
        artifact: str = ARTIFACT_PATH) -> FileResponse:
    '''
    Download the remediated PDF or one of the two validation reports.
    '''
    job = _require_job(job_id)
    path = job.artifact(artifact)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No {artifact} for this job yet.")

    stem = Path(job.original_name).stem
    if artifact == "pdf":
        return FileResponse(path, media_type="application/pdf",
                            filename=job.original_name)
    return FileResponse(path, media_type="application/json",
                        filename=f"{stem}-{artifact}.json")


@app.post("/api/pdf/{job_id}/cancel")
async def cancel(job_id: str = JOB_ID_PATH) -> dict[str, Any]:
    '''
    Ask a running job to stop at its next stage boundary.
    '''
    job = _require_job(job_id)
    if not registry().cancel(job_id):
        raise HTTPException(
            status_code=409, detail=f"This job has already finished ({job.state})."
        )
    return {"job_id": job_id, "cancelling": True}


@app.delete("/api/pdf/{job_id}")
async def delete(job_id: str = JOB_ID_PATH) -> dict[str, Any]:
    '''
    Forget a job and delete its artifacts.
    '''
    _require_job(job_id)
    registry().remove(job_id)
    return {"job_id": job_id, "deleted": True}


def _require_job(job_id: str):
    '''
    Return a job or raise a 404.
    '''
    job = registry().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job.")
    return job


def _stream_upload(file: UploadFile, destination: Path) -> None:
    '''
    Write an upload to disk, refusing anything over the size cap.
    '''
    total = 0
    file.file.seek(0)
    with destination.open("wb") as handle:
        while chunk := file.file.read(UPLOAD_CHUNK_BYTES):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise ValueError(f"File exceeds the {MAX_UPLOAD_BYTES} byte limit.")
            handle.write(chunk)
    if total == 0:
        raise ValueError("File is empty.")
