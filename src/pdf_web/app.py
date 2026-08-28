'''
FastAPI application exposing the PDF remediation pipeline to a browser.
'''

from __future__ import annotations

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from . import APP_NAME, APP_VERSION
from .bundle import build_bundle
from .config import (
    ALLOWED_CONFIG_FILES,
    CONFIG_DIR,
    DEFAULT_CONFIG_FILE,
    JOBS_ROOT,
    MAX_FILES,
    MAX_JOB_BYTES,
    MIN_FREE_DISK_BYTES,
    RETENTION_SWEEP_SECONDS,
    SSE_KEEPALIVE_SECONDS,
    SSE_POLL_SECONDS,
    STATIC_DIR,
)
from .environment import cached_health
from .models import Job, JobStatus, UploadedFile
from .runner import PipelineRunner, seed_source_folder
from .store import (
    JobStore,
    is_valid_job_id,
    load_persisted_jobs,
    save_meta,
    sweep_expired_jobs,
)
from .uploads import (
    UploadError,
    looks_like_pdf,
    sanitize_upload_name,
    write_upload_stream,
)

JOB_ID_PATH = PathParam(..., pattern=r"^\d{8}-\d{6}-[0-9a-f]{6}$")
FILE_ID_PATH = PathParam(..., pattern=r"^\d{3}$")

STORE = JobStore()
RUNNER = PipelineRunner(STORE)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    '''
    Load persisted jobs, start the worker, and clean up on shutdown.
    '''
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    load_persisted_jobs(STORE)
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
    '''
    Delete expired job directories on a slow timer.
    '''
    while True:
        await asyncio.sleep(RETENTION_SWEEP_SECONDS)
        await asyncio.to_thread(sweep_expired_jobs, STORE)


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    '''
    Serve the single-page frontend.
    '''
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=500, detail="Frontend asset is missing.")
    # The page carries its own script, so a cached copy silently pins the UI to
    # an old build after an upgrade.
    return HTMLResponse(
        index_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"}
    )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    '''
    Report on the external tools the pipeline needs.
    '''
    payload = await asyncio.to_thread(cached_health)
    return {**payload, "queue_depth": RUNNER.queue_depth()}


@app.get("/api/config-files")
async def config_files() -> dict[str, Any]:
    '''
    List the remediation configurations offered in the browser.
    '''
    return {
        "default": DEFAULT_CONFIG_FILE,
        "files": [
            {"name": name, "available": (CONFIG_DIR / name).is_file()}
            for name in ALLOWED_CONFIG_FILES
        ],
    }


@app.get("/api/jobs")
async def list_jobs() -> dict[str, Any]:
    '''
    List known jobs, newest first.
    '''
    return {
        "jobs": [
            {
                "job_id": job.job_id,
                "status": str(job.status),
                "queued": job.status == JobStatus.QUEUED,
                "created_at": job.created_at.isoformat(timespec="seconds"),
                "file_count": len(job.files),
                "config_file": job.config_file,
            }
            for job in STORE.list_jobs()
        ]
    }


@app.post("/api/jobs", status_code=201)
async def create_job(  # pylint: disable=too-many-locals
        files: list[UploadFile] = File(...),
        config_file: str = Form(DEFAULT_CONFIG_FILE),
        skip_font_fix: bool = Form(False),
        wcag_and_ua1_must_pass: bool = Form(False),
        verbose: bool = Form(False)) -> JSONResponse:
    '''
    Accept uploaded PDFs and queue a pipeline run.
    '''
    if config_file not in ALLOWED_CONFIG_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown configuration file: {config_file}"
        )
    if not (CONFIG_DIR / config_file).is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Configuration file is missing on disk: {config_file}"
        )

    incoming = [upload for upload in files if upload.filename]
    if not incoming:
        raise HTTPException(status_code=400, detail="Attach at least one PDF.")
    if len(incoming) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Attach at most {MAX_FILES} PDFs per job."
        )

    _assert_disk_space()

    job = Job(
        job_id=_new_job_id(),
        created_at=datetime.now(),
        config_file=config_file,
        skip_font_fix=skip_font_fix,
        wcag_and_ua1_must_pass=wcag_and_ua1_must_pass,
        verbose=verbose,
    )
    job.source_path.mkdir(parents=True, exist_ok=True)
    job.web_path.mkdir(parents=True, exist_ok=True)

    try:
        await _store_uploads(job, incoming)
    except UploadError as error:
        shutil.rmtree(job.base_path, ignore_errors=True)
        status_code = 413 if "limit" in str(error) else 400
        raise HTTPException(status_code=status_code, detail=str(error)) from error
    except Exception as error:  # pylint: disable=broad-exception-caught
        shutil.rmtree(job.base_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=str(error)) from error

    STORE.add(job)
    save_meta(job)
    queue_position = RUNNER.submit(job.job_id)

    return JSONResponse(
        status_code=201,
        content={**job.to_dict(), "queue_position": queue_position}
    )


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str = JOB_ID_PATH, since: int = 0) -> dict[str, Any]:
    '''
    Return a job's state, optionally with events recorded after a cursor.
    '''
    job = _require_job(job_id)
    cursor, events = STORE.events_since(job_id, since)
    return {**job.to_dict(), "cursor": cursor, "events": events}


@app.get("/api/jobs/{job_id}/events")
async def job_events(request: Request, job_id: str = JOB_ID_PATH, since: int = 0):
    '''
    Stream job events as Server-Sent Events.
    '''
    _require_job(job_id)

    async def event_stream() -> AsyncIterator[str]:
        '''
        Poll the store and forward new events to the browser.
        '''
        cursor = since
        idle_seconds = 0.0
        while True:
            if await request.is_disconnected():
                return

            cursor, events = STORE.events_since(job_id, cursor)
            if events:
                idle_seconds = 0.0
                for event in events:
                    yield _format_sse(event)
                    if event["type"] == "done":
                        return
            else:
                idle_seconds += SSE_POLL_SECONDS
                if idle_seconds >= SSE_KEEPALIVE_SECONDS:
                    idle_seconds = 0.0
                    yield ": keepalive\n\n"

                job = STORE.get(job_id)
                if job is not None and job.is_terminal():
                    yield _format_sse({
                        "cursor": cursor,
                        "type": "done",
                        "payload": {"status": str(job.status)},
                    })
                    return

            await asyncio.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs/{job_id}/log")
async def job_log(job_id: str = JOB_ID_PATH) -> FileResponse:
    '''
    Download the captured pipeline log.
    '''
    job = _require_job(job_id)
    log_path = _require_file(job.log_path)
    return FileResponse(
        log_path,
        media_type="text/plain; charset=utf-8",
        filename=f"{job.job_id}-pipeline.log"
    )


@app.get("/api/jobs/{job_id}/files/{file_id}/pdf")
async def download_pdf(
        job_id: str = JOB_ID_PATH,
        file_id: str = FILE_ID_PATH) -> FileResponse:
    '''
    Download one file's remediated PDF.
    '''
    job = _require_job(job_id)
    uploaded_file = _require_upload(job, file_id)
    result = job.find_result(file_id)
    if result is None or result.final_pdf_path is None:
        raise HTTPException(status_code=404, detail="No output PDF for this file.")

    pdf_path = _require_file(result.final_pdf_path)
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=uploaded_file.original_name
    )


@app.get("/api/jobs/{job_id}/files/{file_id}/{stage}")
async def download_report(
        job_id: str = JOB_ID_PATH,
        file_id: str = FILE_ID_PATH,
        stage: str = PathParam(..., pattern=r"^(before|after)$")) -> FileResponse:
    '''
    Download one file's normalized validation report.
    '''
    job = _require_job(job_id)
    uploaded_file = _require_upload(job, file_id)
    report_path = _require_file(job.result_folder(file_id) / f"{stage}.json")
    return FileResponse(
        report_path,
        media_type="application/json",
        filename=f"{Path(uploaded_file.original_name).stem}-{stage}.json"
    )


@app.get("/api/jobs/{job_id}/download")
async def download_bundle(job_id: str = JOB_ID_PATH) -> FileResponse:
    '''
    Download every artifact for a job as one ZIP archive.
    '''
    job = _require_job(job_id)
    if not job.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")

    if not job.bundle_path.is_file():
        await asyncio.to_thread(build_bundle, job, job.bundle_path)

    bundle_path = _require_file(job.bundle_path)
    return FileResponse(
        bundle_path,
        media_type="application/zip",
        filename=f"{job.job_id}-remediation.zip"
    )


@app.post("/api/jobs/{job_id}/retry", status_code=201)
async def retry_job(
        job_id: str = JOB_ID_PATH,
        skip_font_fix: bool = Form(True)) -> JSONResponse:
    '''
    Re-run a finished job's PDFs without asking the browser to upload them again.
    '''
    original = _require_job(job_id)
    if not original.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")

    sources = [
        original.source_path / uploaded_file.stored_name
        for uploaded_file in original.files
    ]
    missing = [path.name for path in sources if not path.is_file()]
    if not sources or missing:
        raise HTTPException(
            status_code=409,
            detail="The original uploads are no longer on disk; upload them again."
        )

    _assert_disk_space()

    job = Job(
        job_id=_new_job_id(),
        created_at=datetime.now(),
        config_file=original.config_file,
        skip_font_fix=skip_font_fix,
        wcag_and_ua1_must_pass=original.wcag_and_ua1_must_pass,
        verbose=original.verbose,
    )
    job.web_path.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(seed_source_folder, job, sources)
    job.files = [
        UploadedFile(
            file_id=uploaded_file.file_id,
            original_name=uploaded_file.original_name,
            stored_name=uploaded_file.stored_name,
            size_bytes=uploaded_file.size_bytes,
        )
        for uploaded_file in original.files
    ]

    STORE.add(job)
    save_meta(job)
    queue_position = RUNNER.submit(job.job_id)
    return JSONResponse(
        status_code=201,
        content={**job.to_dict(), "queue_position": queue_position}
    )


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str = JOB_ID_PATH) -> dict[str, Any]:
    '''
    Delete a finished job and everything it produced.
    '''
    job = _require_job(job_id)
    if not job.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")

    STORE.remove(job_id)
    await asyncio.to_thread(shutil.rmtree, job.base_path, True)
    return {"job_id": job_id, "deleted": True}


async def _store_uploads(job: Job, incoming: list[UploadFile]) -> None:
    '''
    Sanitize and write every upload into the job's source folder.
    '''
    taken_names: set[str] = set()
    total_bytes = 0

    for position, upload in enumerate(incoming):
        original_name = upload.filename or f"upload-{position}.pdf"
        stored_name = sanitize_upload_name(original_name, taken_names)
        destination = job.source_path / stored_name

        size_bytes = await asyncio.to_thread(
            write_upload_stream,
            _iterate_upload(upload),
            destination,
            original_name
        )

        if not looks_like_pdf(destination):
            destination.unlink(missing_ok=True)
            raise UploadError(f"File is not a PDF: {original_name}")

        total_bytes += size_bytes
        if total_bytes > MAX_JOB_BYTES:
            raise UploadError(
                f"Uploads exceed the {MAX_JOB_BYTES} byte per-job limit."
            )

        job.files.append(UploadedFile(
            file_id=f"{position:03d}",
            original_name=original_name,
            stored_name=stored_name,
            size_bytes=size_bytes,
        ))


def _iterate_upload(upload: UploadFile):
    '''
    Yield an upload's contents in chunks from its synchronous file object.
    '''
    upload.file.seek(0)
    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            return
        yield chunk


def _new_job_id() -> str:
    '''
    Return a sortable, filesystem-safe job identifier.
    '''
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"


def _require_job(job_id: str) -> Job:
    '''
    Return a job or raise a 404.
    '''
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="Unknown job.")
    job = STORE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job.")
    return job


def _require_upload(job: Job, file_id: str) -> UploadedFile:
    '''
    Return an uploaded file record or raise a 404.
    '''
    uploaded_file = job.find_file(file_id)
    if uploaded_file is None:
        raise HTTPException(status_code=404, detail="Unknown file.")
    return uploaded_file


def _require_file(candidate: Path) -> Path:
    '''
    Return an existing path that is contained within the jobs directory.
    '''
    resolved = candidate.resolve()
    if not resolved.is_relative_to(JOBS_ROOT.resolve()) or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Not found.")
    return resolved


def _assert_disk_space() -> None:
    '''
    Refuse new work when the jobs volume is nearly full.
    '''
    try:
        usage = shutil.disk_usage(JOBS_ROOT)
    except OSError:
        return
    if usage.free < MIN_FREE_DISK_BYTES:
        raise HTTPException(
            status_code=507,
            detail="Not enough free disk space to accept a new job."
        )


def _format_sse(event: dict[str, Any]) -> str:
    '''
    Render one event in the Server-Sent Events wire format.
    '''
    data = json.dumps(event, default=str)
    return f"event: {event['type']}\ndata: {data}\n\n"
