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

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

from . import APP_NAME, APP_VERSION
from .bundle import build_bundle
from .config import (
    ALLOWED_CONFIG_FILES,
    CONFIG_DIR,
    CONFIG_FILE_DETAILS,
    DEFAULT_CONFIG_FILE,
    JOBS_ROOT,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_SUBMISSION_BYTES,
    MIN_FREE_DISK_BYTES,
    RETENTION_SWEEP_SECONDS,
    max_concurrent_jobs,
    max_running_jobs_per_user,
    SSE_KEEPALIVE_SECONDS,
    SSE_POLL_SECONDS,
    STATIC_DIR,
)
from .environment import cached_health
from .identity import (
    describe_mode,
    diagnose_request,
    header_diagnostic_enabled,
    resolve_user,
)
from .models import Job, JobStatus, UploadedFile, outcome_label, summarize_report
from .runner import PipelineRunner
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

async def current_user(request: Request) -> str:
    '''
    Return the authenticated user, rejecting unauthenticated requests.
    '''
    return resolve_user(request)


CURRENT_USER = Depends(current_user)
JOB_ID_PATH = PathParam(..., pattern=r"^\d{8}-\d{6}-[0-9a-f]{6}$")
ARTIFACT_PATH = PathParam(..., pattern=r"^(pdf|before|after)$")

STORE = JobStore()
RUNNER = PipelineRunner(STORE)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    '''
    Load persisted jobs, start the worker, and clean up on shutdown.
    '''
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
    '''
    Delete expired job directories on a slow timer.
    '''
    while True:
        await asyncio.sleep(RETENTION_SWEEP_SECONDS)
        await asyncio.to_thread(sweep_expired_jobs, STORE)


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)


def _serve_frontend_asset(filename: str, media_type: str) -> Response:
    '''
    Read a frontend asset from disk and serve it uncached.

    The HTML, CSS, and JS files version together; letting a browser cache
    any one of them separately can pin the UI to a mismatched build after
    an upgrade.
    '''
    path = STATIC_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=500, detail=f"Frontend asset is missing: {filename}")
    return Response(
        path.read_text(encoding="utf-8"),
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", response_class=HTMLResponse)
async def index() -> Response:
    '''
    Serve the single-page frontend shell.
    '''
    return _serve_frontend_asset("index.html", "text/html")


@app.get("/static/style.css")
async def stylesheet() -> Response:
    '''
    Serve the frontend stylesheet.
    '''
    return _serve_frontend_asset("style.css", "text/css")


@app.get("/static/app.js")
async def script() -> Response:
    '''
    Serve the frontend script.
    '''
    return _serve_frontend_asset("app.js", "text/javascript")


@app.get("/healthz")
async def liveness() -> JSONResponse:
    '''
    Report whether the service can do work, for supervisors and load balancers.

    Deliberately unauthenticated and deliberately uninformative: a probe needs
    to know the process is alive and the worker is running, and nothing about
    licences, tooling, or the identity configuration. That detail stays behind
    authentication on /api/health.
    '''
    worker_alive = RUNNER.is_running()
    return JSONResponse(
        status_code=200 if worker_alive else 503,
        content={
            "status": "ok" if worker_alive else "degraded",
            "worker": "running" if worker_alive else "stopped",
            "version": APP_VERSION,
        }
    )


@app.get("/api/proxy-headers")
async def proxy_headers(request: Request) -> dict[str, Any]:
    '''
    Report what the proxy forwarded, for diagnosing a deployment.

    Deliberately reachable without authenticating, because its purpose is to
    explain why authentication is not working. It is disabled unless
    PDF_WEB_HEADER_DIAGNOSTIC is set, returns 404 when off so it is not
    discoverable, and redacts credential-bearing values.
    '''
    if not header_diagnostic_enabled():
        raise HTTPException(status_code=404, detail="Not found.")
    return {"auth": describe_mode(), **diagnose_request(request)}


@app.get("/api/health")
async def health(user: str = CURRENT_USER) -> dict[str, Any]:
    '''
    Report on the external tools the pipeline needs.
    '''
    payload = await asyncio.to_thread(cached_health)
    return {
        **payload,
        "queue_depth": RUNNER.queue_depth(),
        "user": user,
        "auth": describe_mode(),
    }


@app.get("/api/config-files", dependencies=[Depends(current_user)])
async def config_files() -> dict[str, Any]:
    '''
    List the remediation configurations offered in the browser.
    '''
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
                "available": (CONFIG_DIR / name).is_file(),
            }
            for name in ALLOWED_CONFIG_FILES
        ],
    }


@app.get("/api/jobs")
async def list_jobs(user: str = CURRENT_USER) -> dict[str, Any]:
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
                "name": job.file.original_name,
                "outcome": job.outcome,
                "outcome_label": outcome_label(job.outcome),
                "config_file": job.config_file,
            }
            for job in STORE.list_jobs()
            if job.submitted_by == user
        ]
    }


@app.post("/api/jobs", status_code=201)
async def create_job(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        files: list[UploadFile] = File(...),
        config_file: str = Form(DEFAULT_CONFIG_FILE),
        skip_font_fix: bool = Form(False),
        wcag_and_ua1_must_pass: bool = Form(False),
        verbose: bool = Form(False),
        user: str = CURRENT_USER) -> JSONResponse:
    '''
    Accept uploaded PDFs and queue one independent job per file.

    A file that cannot be accepted is reported rather than failing the whole
    submission, so nineteen good PDFs still run when the twentieth is a
    spreadsheet.
    '''
    if config_file not in ALLOWED_CONFIG_FILES:
        raise HTTPException(
            status_code=400, detail=f"Unknown configuration file: {config_file}"
        )
    if not (CONFIG_DIR / config_file).is_file():
        raise HTTPException(
            status_code=400, detail=f"Configuration file is missing: {config_file}"
        )

    incoming = [upload for upload in files if upload.filename]
    if not incoming:
        raise HTTPException(status_code=400, detail="Attach at least one PDF.")
    if len(incoming) > MAX_FILES:
        raise HTTPException(
            status_code=400, detail=f"Attach at most {MAX_FILES} PDFs per submission."
        )

    _assert_disk_space()

    created_at = datetime.now()
    taken_ids: set[str] = set()
    taken_names: set[str] = set()
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    total_bytes = 0

    for upload in incoming:
        original_name = upload.filename or "upload.pdf"
        job = None
        try:
            stored_name = sanitize_upload_name(original_name, taken_names)
            job = Job(
                job_id=_new_job_id(taken_ids),
                created_at=created_at,
                config_file=config_file,
                file=UploadedFile(original_name, stored_name, 0),
                submitted_by=user,
                skip_font_fix=skip_font_fix,
                wcag_and_ua1_must_pass=wcag_and_ua1_must_pass,
                verbose=verbose,
            )
            job.input_path.parent.mkdir(parents=True, exist_ok=True)
            job.web_path.mkdir(parents=True, exist_ok=True)

            size = await asyncio.to_thread(
                write_upload_stream, _iterate_upload(upload),
                job.input_path, original_name
            )
            if not looks_like_pdf(job.input_path):
                raise UploadError(f"File is not a PDF: {original_name}")

            total_bytes += size
            if total_bytes > MAX_SUBMISSION_BYTES:
                raise UploadError(
                    f"Submission exceeds the {MAX_SUBMISSION_BYTES} byte limit."
                )
            job.file.size_bytes = size
        except UploadError as error:
            if job is not None:
                shutil.rmtree(job.base_path, ignore_errors=True)
            rejected.append({"original_name": original_name, "reason": str(error)})
            continue

        STORE.add(job)
        save_meta(job)
        accepted.append({
            **job.to_dict(),
            "jobs_ahead": RUNNER.submit(job.job_id, user),
        })

    if not accepted:
        return JSONResponse(status_code=400, content={
            "detail": "; ".join(
                f"{r['original_name']}: {r['reason']}" for r in rejected
            ),
            "rejected": rejected,
        })

    return JSONResponse(status_code=201, content={
        "jobs": accepted,
        "rejected": rejected,
        "concurrency": max_concurrent_jobs(),
        "your_limit": max_running_jobs_per_user(),
    })


@app.get("/api/queue")
async def queue_view(user: str = CURRENT_USER) -> dict[str, Any]:
    '''
    Summarize the caller's jobs in one small payload.

    A browser watching twenty jobs cannot open twenty event streams: it would
    exhaust the per-origin connection limit and starve the rest of the page. So
    the list view polls this, and the detail view keeps the single stream.
    '''
    jobs = [job for job in STORE.list_jobs() if job.submitted_by == user]
    running = sum(1 for job in jobs if job.status == JobStatus.RUNNING)
    return {
        "concurrency": max_concurrent_jobs(),
        "your_limit": max_running_jobs_per_user(),
        "your_running": running,
        "all_terminal": all(job.is_terminal() for job in jobs),
        "jobs": [
            {
                "job_id": job.job_id,
                "name": job.file.original_name,
                "created_at": job.created_at.isoformat(timespec="seconds"),
                "config_file": job.config_file,
                "config_label": CONFIG_FILE_DETAILS.get(job.config_file, {}).get(
                    "label", job.config_file
                ),
                "status": str(job.status),
                "outcome": job.outcome,
                "outcome_label": outcome_label(job.outcome),
                "stages_done": len(job.stages),
                "current_stage": job.stages[-1]["name"] if job.stages else None,
                "jobs_ahead": RUNNER.jobs_ahead(job.job_id),
                "before": summarize_report(job.result.before if job.result else None),
                "after": summarize_report(job.result.after if job.result else None),
                "has_pdf": job.artifact("pdf") is not None,
                "error": job.error,
            }
            for job in jobs
        ],
    }


@app.get("/api/jobs/{job_id}")
async def get_job(
        job_id: str = JOB_ID_PATH,
        since: int = 0,
        user: str = CURRENT_USER) -> dict[str, Any]:
    '''
    Return a job's state, optionally with events recorded after a cursor.
    '''
    job = _require_job(job_id, user)
    cursor, events = STORE.events_since(job_id, since)
    return {
        **job.to_dict(),
        "cursor": cursor,
        "events": events,
        "jobs_ahead": RUNNER.jobs_ahead(job_id),
    }


@app.get("/api/jobs/{job_id}/events")
async def job_events(
        request: Request,
        job_id: str = JOB_ID_PATH,
        since: int = 0,
        user: str = CURRENT_USER):
    '''
    Stream job events as Server-Sent Events.
    '''
    _require_job(job_id, user)

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
async def job_log(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER) -> FileResponse:
    '''
    Download the captured pipeline log.
    '''
    job = _require_job(job_id, user)
    log_path = _require_file(job.log_path)
    return FileResponse(
        log_path,
        media_type="text/plain; charset=utf-8",
        filename=f"{job.job_id}-pipeline.log"
    )


@app.get("/api/jobs/{job_id}/download")
async def download_bundle(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER) -> FileResponse:
    '''
    Download every artifact for a job as one ZIP archive.
    '''
    job = _require_job(job_id, user)
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


@app.get("/api/jobs/{job_id}/{artifact}")
async def download_artifact(
        job_id: str = JOB_ID_PATH,
        artifact: str = ARTIFACT_PATH,
        user: str = CURRENT_USER) -> FileResponse:
    '''
    Download the remediated PDF or one of the two validation reports.
    '''
    job = _require_job(job_id, user)
    path = job.artifact(artifact)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No {artifact} for this job.")

    _require_file(path)
    if artifact == "pdf":
        return FileResponse(path, media_type="application/pdf",
                            filename=job.file.original_name)
    stem = Path(job.file.original_name).stem
    return FileResponse(path, media_type="application/json",
                        filename=f"{stem}-{artifact}.json")


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER) -> dict[str, Any]:
    '''
    Stop one of your queued or running jobs.

    Without this, a mistaken batch cannot be stopped: it holds the submitter's
    only slot and everyone queued behind it waits for work nobody wants.
    '''
    job = _require_job(job_id, user)
    if job.is_terminal():
        raise HTTPException(
            status_code=409,
            detail=f"This job has already finished ({job.status})."
        )

    if not await asyncio.to_thread(RUNNER.cancel, job_id):
        raise HTTPException(
            status_code=409,
            detail="This job finished before it could be cancelled."
        )

    return {"job_id": job_id, "status": str(job.status)}


@app.post("/api/jobs/{job_id}/retry", status_code=201)
async def retry_job(
        job_id: str = JOB_ID_PATH,
        skip_font_fix: bool = Form(True),
        user: str = CURRENT_USER) -> JSONResponse:
    '''
    Re-run a finished job's PDF without asking the browser to upload it again.
    '''
    original = _require_job(job_id, user)
    if not original.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")
    if not original.input_path.is_file():
        raise HTTPException(
            status_code=409,
            detail="The original upload is no longer on disk; upload it again.",
        )

    _assert_disk_space()

    job = Job(
        job_id=_new_job_id(set()),
        created_at=datetime.now(),
        config_file=original.config_file,
        file=UploadedFile(
            original.file.original_name,
            original.file.stored_name,
            original.file.size_bytes,
        ),
        submitted_by=user,
        skip_font_fix=skip_font_fix,
        wcag_and_ua1_must_pass=original.wcag_and_ua1_must_pass,
        verbose=original.verbose,
    )
    job.input_path.parent.mkdir(parents=True, exist_ok=True)
    job.web_path.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copy2, original.input_path, job.input_path)

    STORE.add(job)
    save_meta(job)
    return JSONResponse(status_code=201, content={
        **job.to_dict(), "jobs_ahead": RUNNER.submit(job.job_id, user)
    })


@app.delete("/api/jobs")
async def delete_jobs(user: str = CURRENT_USER) -> dict[str, Any]:
    '''Delete every terminal job owned by the current user.'''
    jobs = [job for job in STORE.list_jobs() if job.submitted_by == user]
    deleted: list[str] = []
    skipped: list[str] = []
    for job in jobs:
        if not job.is_terminal():
            skipped.append(job.job_id)
            continue
        STORE.remove(job.job_id)
        await asyncio.to_thread(shutil.rmtree, job.base_path, True)
        deleted.append(job.job_id)
    return {"deleted": deleted, "skipped": skipped}


@app.delete("/api/jobs/{job_id}")
async def delete_job(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER) -> dict[str, Any]:
    '''
    Delete a finished job and everything it produced.
    '''
    job = _require_job(job_id, user)
    if not job.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")

    STORE.remove(job_id)
    await asyncio.to_thread(shutil.rmtree, job.base_path, True)
    return {"job_id": job_id, "deleted": True}


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


def _new_job_id(taken: set[str]) -> str:
    '''
    Return a sortable, filesystem-safe identifier no other job is using.

    A submission mints many identifiers inside one second, so the timestamp
    stops distinguishing them and the random suffix has to be checked.
    '''
    while True:
        candidate = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
        if candidate in taken or STORE.get(candidate) is not None:
            continue
        if (JOBS_ROOT / candidate).exists():
            continue
        taken.add(candidate)
        return candidate


def _require_job(job_id: str, user: str) -> Job:
    """
    Return a job owned by the given user, or raise a 404.

    Someone else's job is reported as missing rather than forbidden, so job
    identifiers cannot be probed for existence.
    """
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="Unknown job.")
    job = STORE.get(job_id)
    if job is None or job.submitted_by != user:
        raise HTTPException(status_code=404, detail="Unknown job.")
    return job


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
