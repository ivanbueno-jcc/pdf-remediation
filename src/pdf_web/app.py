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

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
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
)
from .environment import cached_health, collect_readiness
from .assets import serve_asset, serve_index, serve_versioned_asset
from .identity import (
    describe_mode,
    diagnose_request,
    header_diagnostic_enabled,
    resolve_user,
)
from .models import Job, JobAccessSnapshot, JobStatus, UploadedFile, outcome_label
from .job_views import queue_payload
from .runner import PipelineRunner
from .submission import prepare_uploaded_job, validate_options
from .store import (
    JobStore,
    is_valid_job_id,
    load_persisted_jobs,
    save_meta,
    sweep_expired_jobs,
)

async def current_user(request: Request) -> str:
    '''
    Return the authenticated user, rejecting unauthenticated requests.
    '''
    return resolve_user(request)


CURRENT_USER = Depends(current_user)
JOB_ID_PATH = PathParam(..., pattern=r"^\d{8}-\d{6}-[0-9a-f]{6}$")
ARTIFACT_PATH = PathParam(..., pattern=r"^(pdf|before|after)$")
ASSET_VERSION_PATH = PathParam(..., pattern=r"^[0-9a-f]{12}$")

STORE = JobStore()
RUNNER = PipelineRunner(STORE)
QUEUE_PAGE_SIZE = 100


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


@app.get("/", response_class=HTMLResponse)
async def index() -> Response:
    '''
    Serve the single-page frontend shell.
    '''
    return serve_index()


@app.get("/static/style.css")
async def stylesheet() -> Response:
    '''
    Serve the legacy unversioned stylesheet URL.
    '''
    return serve_asset("style.css", "text/css")


@app.get("/static/app.js")
async def script() -> Response:
    '''
    Serve the legacy unversioned script URL.
    '''
    return serve_asset("app.js", "text/javascript")


@app.get("/static/style.{version}.css")
async def versioned_stylesheet(version: str = ASSET_VERSION_PATH) -> Response:
    '''Serve a content-versioned stylesheet with immutable caching.'''
    return serve_versioned_asset("style.css", version, "text/css")


@app.get("/static/app.{version}.js")
async def versioned_script(version: str = ASSET_VERSION_PATH) -> Response:
    '''Serve a content-versioned script with immutable caching.'''
    return serve_versioned_asset("app.js", version, "text/javascript")


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


@app.get("/readyz")
async def readiness() -> JSONResponse:
    '''Report deployment readiness without exposing dependency details.'''
    result = await asyncio.to_thread(collect_readiness)
    ready = bool(result["ready"])
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not-ready", "version": APP_VERSION},
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
    payload, readiness_payload = await asyncio.gather(
        asyncio.to_thread(cached_health),
        asyncio.to_thread(collect_readiness),
    )
    return {
        **payload,
        "deployment_readiness": readiness_payload,
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
    jobs = []
    for job in STORE.list_queue_snapshots(user):
        jobs.append({
            "job_id": job.job_id,
            "status": str(job.status),
            "queued": job.status == JobStatus.QUEUED,
            "created_at": job.created_at.isoformat(timespec="seconds"),
            "page_count": job.page_count,
            "name": job.name,
            "outcome": job.outcome,
            "outcome_label": outcome_label(job.outcome),
            "config_file": job.config_file,
        })
    return {"jobs": jobs}


@app.post("/api/jobs", status_code=201)
async def create_job(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals,too-many-branches,too-many-statements
        files: list[UploadFile] = File(...),
        config_file: str = Form(DEFAULT_CONFIG_FILE),
        attempt_unlock: bool = Form(True),
        attempt_fix: bool = Form(True),
        attempt_font_fix: bool | None = Form(None),
        attempt_targeted_fixes: bool = Form(True),
        skip_font_fix: bool = Form(False),
        require_wcag: bool = Form(True),
        require_pdfua1: bool = Form(False),
        wcag_and_ua1_must_pass: bool | None = Form(None),
        verbose: bool = Form(False),
        user: str = CURRENT_USER) -> JSONResponse:
    '''
    Accept uploaded PDFs and queue one independent job per file.

    A file that cannot be accepted is reported rather than failing the whole
    submission, so nineteen good PDFs still run when the twentieth is a
    spreadsheet.
    '''
    options = validate_options(
        config_file, attempt_unlock, attempt_fix, attempt_font_fix, skip_font_fix,
        attempt_targeted_fixes, require_wcag, require_pdfua1,
        wcag_and_ua1_must_pass, verbose,
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
    accepted_jobs: list[Job] = []

    try:
        for upload in incoming:
            original_name = upload.filename or "upload.pdf"
            job, error, size = await prepare_uploaded_job(
                upload, options, user, created_at, taken_ids, taken_names, total_bytes,
                _new_job_id,
            )
            if error is not None or job is None:
                rejected.append({
                    "original_name": original_name,
                    "reason": error or "Upload rejected.",
                })
                continue
            total_bytes += size
            accepted_jobs.append(job)

        # Complete all disk work before making any job visible to the runner or
        # connected clients. A failure here leaves no registered jobs behind.
        for job in accepted_jobs:
            save_meta(job)

        registered_ids: list[str] = []
        try:
            job_ids = tuple(job.job_id for job in accepted_jobs)
            STORE.add_batch(tuple(accepted_jobs), notify=False)
            registered_ids.extend(job_ids)
            jobs_ahead = RUNNER.submit_batch(
                job_ids, user
            )
            STORE.publish_job_added(job_ids)
        except Exception:
            for job_id in registered_ids:
                STORE.remove(job_id, notify=False)
            raise
        accepted = [{**job.to_dict()} for job in accepted_jobs]
    except Exception:
        await _remove_submission_jobs(accepted_jobs)
        raise

    if not accepted:
        return JSONResponse(status_code=400, content={
            "detail": "; ".join(
                f"{r['original_name']}: {r['reason']}" for r in rejected
            ),
            "rejected": rejected,
        })

    for payload, ahead in zip(accepted, jobs_ahead):
        payload["jobs_ahead"] = ahead

    return JSONResponse(status_code=201, content={
        "jobs": accepted,
        "rejected": rejected,
        "concurrency": max_concurrent_jobs(),
        "your_limit": max_running_jobs_per_user(),
    })


async def _remove_submission_jobs(jobs: list[Job]) -> None:
    '''Remove prepared job directories after a failed submission commit.'''
    if not jobs:
        return
    await asyncio.gather(*(
        asyncio.to_thread(shutil.rmtree, job.base_path, True)
        for job in jobs
    ))


@app.get("/api/queue")
async def queue_view(
        cursor: str | None = Query(None),
        limit: int = Query(100, ge=1, le=200),
        user: str = CURRENT_USER) -> dict[str, Any]:
    '''
    Summarize one page of the caller's jobs.

    Return a paginated HTTP snapshot for non-streaming clients and recovery
    actions. Live browsers receive their initial state and updates from the
    owner-scoped SSE endpoint below.
    '''
    return _queue_snapshot(user, cursor, limit)


def _queue_snapshot(
        user: str,
        cursor: str | None = None,
        limit: int | None = 100) -> dict[str, Any]:
    '''Build the owner-scoped queue projection used by HTTP and SSE.'''
    try:
        jobs, total_jobs, next_cursor = STORE.list_jobs_for_user(user, cursor, limit)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    pending_positions = RUNNER.pending_positions_for(
        {job.job_id for job in jobs}
    )
    your_running, has_active = RUNNER.user_activity(user)
    return {
        "concurrency": max_concurrent_jobs(),
        "your_limit": max_running_jobs_per_user(),
        "your_running": your_running,
        "all_terminal": not has_active,
        "total_jobs": total_jobs,
        "queue_generation": RUNNER.queue_generation(),
        "next_cursor": next_cursor,
        "jobs": [queue_payload(job, pending_positions) for job in jobs],
    }


def _queue_meta(user: str) -> dict[str, Any]:
    '''Build queue metadata without copying any job results.'''
    your_running, has_active = RUNNER.user_activity(user)
    return {
        "concurrency": max_concurrent_jobs(),
        "your_limit": max_running_jobs_per_user(),
        "your_running": your_running,
        "all_terminal": not has_active,
        "total_jobs": STORE.owner_job_count(user),
        "queue_generation": RUNNER.queue_generation(),
    }


@app.get("/api/queue/events")
async def queue_events(
        request: Request,
        user: str = CURRENT_USER):
    '''Stream owner-scoped queue snapshots whenever job state changes.'''

    async def event_stream() -> AsyncIterator[str]:  # pylint: disable=too-many-branches
        '''Wait on store changes instead of polling the queue endpoint.'''
        subscriber_id, updates_queue = STORE.subscribe_owner(user)
        first = True
        try:
            while True:
                if await request.is_disconnected():
                    return
                if first:
                    yield "event: queue\ndata: " + json.dumps(
                        _queue_snapshot(user, limit=QUEUE_PAGE_SIZE), separators=(",", ":")
                    ) + "\n\n"
                    first = False
                try:
                    updates = await asyncio.wait_for(
                        updates_queue.get_batch(), timeout=SSE_KEEPALIVE_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                changed_ids = {
                    job_id for update_type, job_id in updates
                    if update_type != "job-removed"
                }
                if any(update_type == "queue-changed" for update_type, _ in updates):
                    changed_ids.update(STORE.active_job_ids(user))
                positions = RUNNER.pending_positions_for(changed_ids)
                latest: dict[str, str] = {}
                for update_type, job_id in updates:
                    if update_type != "queue-changed":
                        latest[job_id] = update_type
                for job_id in changed_ids:
                    latest.setdefault(job_id, "job-updated")
                for job_id, update_type in latest.items():
                    if update_type == "job-removed":
                        payload = {"job_id": job_id}
                    else:
                        job = STORE.queue_snapshot(job_id)
                        if job is None:
                            continue
                        payload = queue_payload(job, positions)
                    yield "event: " + update_type + "\ndata: " + json.dumps(
                        payload, separators=(",", ":")
                    ) + "\n\n"
                yield "event: queue-meta\ndata: " + json.dumps(
                    _queue_meta(user), separators=(",", ":")
                ) + "\n\n"
        finally:
            STORE.unsubscribe_owner(user, subscriber_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs/{job_id}/details")
async def job_details(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER) -> dict[str, Any]:
    '''
    Return the job detail view without the live queue stream.

    Live updates are delivered through the owner-scoped queue SSE stream.
    Keeping that stream out of this response makes the expandable detail panel
    independent of connection-level event state.
    '''
    job = _require_job_snapshot(job_id, user)
    return job.to_dict()


@app.get("/api/jobs/{job_id}/log")
async def job_log(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER) -> FileResponse:
    '''
    Download the captured pipeline log.
    '''
    job = _require_job_access(job_id, user)
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
    job = _require_job_access(job_id, user)
    if not job.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")

    with STORE.job_artifact_lock(job_id) as job_exists:
        if not job_exists:
            raise HTTPException(status_code=404, detail="Not found.")
        if not job.bundle_path.is_file():
            bundle_job = STORE.snapshot(job_id)
            if bundle_job is None:
                raise HTTPException(status_code=404, detail="Not found.")
            await asyncio.to_thread(build_bundle, bundle_job, job.bundle_path)

    bundle_path = _require_file(job.bundle_path)
    return FileResponse(
        bundle_path,
        media_type="application/zip",
        filename=f"{job.job_id}-remediation.zip"
    )


@app.get("/api/jobs/{job_id}/original")
async def open_original(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER) -> FileResponse:
    '''Open the original uploaded PDF in the browser.'''
    job = _require_job_access(job_id, user)
    path = _require_file(job.input_path)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@app.get("/api/jobs/{job_id}/{artifact}")
async def download_artifact(
        job_id: str = JOB_ID_PATH,
        artifact: str = ARTIFACT_PATH,
        user: str = CURRENT_USER) -> FileResponse:
    '''
    Download the remediated PDF or one of the two validation reports.
    '''
    job = _require_job_access(job_id, user)
    path = job.artifact(artifact)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No {artifact} for this job.")

    _require_file(path)
    if artifact == "pdf":
        return FileResponse(path, media_type="application/pdf",
                            filename=job.original_name)
    stem = Path(job.original_name).stem
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
    job = _require_job_access(job_id, user)
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
    original = _require_job_access(job_id, user)
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
            original.original_name,
            original.stored_name,
            original.size_bytes,
        ),
        submitted_by=user,
        attempt_unlock=original.attempt_unlock,
        attempt_fix=original.attempt_fix,
        skip_font_fix=skip_font_fix,
        attempt_targeted_fixes=original.attempt_targeted_fixes,
        require_wcag="wcag" in original.required_profiles(),
        require_pdfua1="ua1" in original.required_profiles(),
        verbose=original.verbose,
    )
    job.input_path.parent.mkdir(parents=True, exist_ok=True)
    job.web_path.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copy2, original.input_path, job.input_path)

    save_meta(job)
    try:
        STORE.add_batch((job,), notify=False)
        jobs_ahead = RUNNER.submit(job.job_id, user)
        STORE.publish_job_added((job.job_id,))
    except Exception:
        STORE.remove(job.job_id, notify=False)
        await asyncio.to_thread(shutil.rmtree, job.base_path, True)
        raise
    return JSONResponse(status_code=201, content={
        **job.to_dict(), "jobs_ahead": jobs_ahead
    })


@app.delete("/api/jobs")
async def delete_jobs(user: str = CURRENT_USER) -> dict[str, Any]:
    '''Delete every terminal job owned by the current user.'''
    jobs = STORE.list_access_snapshots(user)
    deleted: list[str] = []
    skipped: list[str] = []
    for job in jobs:
        if not job.is_terminal():
            skipped.append(job.job_id)
            continue
        with STORE.job_artifact_lock(job.job_id):
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
    job = _require_job_access(job_id, user)
    if not job.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")

    with STORE.job_artifact_lock(job_id):
        STORE.remove(job_id)
        await asyncio.to_thread(shutil.rmtree, job.base_path, True)
    return {"job_id": job_id, "deleted": True}


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


def _require_job_access(job_id: str, user: str) -> JobAccessSnapshot:
    """
    Return a job owned by the given user, or raise a 404.

    Someone else's job is reported as missing rather than forbidden, so job
    identifiers cannot be probed for existence.
    """
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="Unknown job.")
    job = STORE.access_snapshot(job_id)
    if job is None or job.submitted_by != user:
        raise HTTPException(status_code=404, detail="Unknown job.")
    return job


def _require_job_snapshot(job_id: str, user: str) -> Job:
    '''Return a full job snapshot for responses that need complete state.'''
    if not is_valid_job_id(job_id):
        raise HTTPException(status_code=404, detail="Unknown job.")
    job = STORE.snapshot(job_id)
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
