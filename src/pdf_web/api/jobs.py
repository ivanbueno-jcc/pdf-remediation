'''Routes for submissions, the owner-scoped queue, and job lifecycle actions.'''

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import (
    DEFAULT_CONFIG_FILE,
    MAX_FILES,
    SSE_KEEPALIVE_SECONDS,
    max_concurrent_jobs,
    max_running_jobs_per_user,
)
from ..identity import resolve_user
from ..job_views import queue_payload
from ..models import JobSerializer
from ..services.job_service import JobAccessService, JobWorkflow
from ..services.submission_service import SubmissionService
from ..submission import validate_options
from .runtime import ApiRuntime, get_runtime
from .schemas import JobResponse, QueuePageResponse, SubmissionResponse

router = APIRouter()


async def current_user(request: Request) -> str:
    '''Resolve the authenticated identity for all job routes.'''
    return resolve_user(request)


CURRENT_USER = Depends(current_user)
CURRENT_RUNTIME = Depends(get_runtime)
JOB_ID_PATH = PathParam(..., pattern=r"^\d{8}-\d{6}-[0-9a-f]{6}$")
QUEUE_PAGE_SIZE = 100
SSE_DISCONNECT_POLL_SECONDS = 0.5


@router.get("/api/jobs", response_model=QueuePageResponse, deprecated=True)
async def list_jobs(
        cursor: str | None = Query(None),
        limit: int = Query(100, ge=1, le=200),
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> dict[str, Any]:
    '''Deprecated paginated alias; use /api/queue for queue snapshots.'''
    return _queue_snapshot(runtime, user, cursor, limit)


@router.post("/api/jobs", status_code=201, response_model=SubmissionResponse)
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
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> JSONResponse:
    '''Prepare, persist, and queue one independent job per accepted PDF.'''
    options = validate_options(
        config_file, attempt_unlock, attempt_fix, attempt_font_fix, skip_font_fix,
        attempt_targeted_fixes, require_wcag, require_pdfua1,
        wcag_and_ua1_must_pass, verbose, runtime.config_dir,
    )
    incoming = [upload for upload in files if upload.filename]
    if not incoming:
        raise HTTPException(status_code=400, detail="Attach at least one PDF.")
    if len(incoming) > MAX_FILES:
        raise HTTPException(
            status_code=400, detail=f"Attach at most {MAX_FILES} PDFs per submission."
        )

    runtime.assert_disk_space()
    submission = SubmissionService(runtime.store, runtime.runner)
    accepted_jobs, rejected = await submission.prepare_batch(
        incoming, options, user, runtime.new_job_id, runtime.jobs_root
    )
    jobs_ahead = await submission.commit(accepted_jobs, user)
    accepted = [JobSerializer.from_record(job) for job in accepted_jobs]
    if not accepted:
        return JSONResponse(status_code=400, content={
            "detail": "; ".join(
                f"{item['original_name']}: {item['reason']}" for item in rejected
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


def _queue_snapshot(
        runtime: ApiRuntime,
        owner: str,
        cursor: str | None = None,
        limit: int | None = 100) -> dict[str, Any]:
    '''Build an owner-scoped queue page for HTTP and SSE consumers.'''
    try:
        jobs, total_jobs, next_cursor = runtime.store.list_jobs_for_user(
            owner, cursor, limit
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    pending = runtime.runner.pending_positions_for({job.job_id for job in jobs})
    your_running, has_active = runtime.runner.user_activity(owner)
    return {
        "concurrency": max_concurrent_jobs(),
        "your_limit": max_running_jobs_per_user(),
        "your_running": your_running,
        "all_terminal": not has_active,
        "total_jobs": total_jobs,
        "processed_jobs": runtime.store.owner_processed_job_count(owner),
        "queue_generation": runtime.runner.queue_generation(),
        "next_cursor": next_cursor,
        "jobs": [queue_payload(job, pending) for job in jobs],
    }


def _queue_meta(runtime: ApiRuntime, owner: str) -> dict[str, Any]:
    '''Build queue metadata without copying job result details.'''
    your_running, has_active = runtime.runner.user_activity(owner)
    return {
        "concurrency": max_concurrent_jobs(),
        "your_limit": max_running_jobs_per_user(),
        "your_running": your_running,
        "all_terminal": not has_active,
        "total_jobs": runtime.store.owner_job_count(owner),
        "processed_jobs": runtime.store.owner_processed_job_count(owner),
        "queue_generation": runtime.runner.queue_generation(),
    }


@router.get("/api/queue", response_model=QueuePageResponse)
async def queue_view(
        cursor: str | None = Query(None),
        limit: int = Query(100, ge=1, le=200),
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> dict[str, Any]:
    '''Return a paginated snapshot of the caller's queue.'''
    return _queue_snapshot(runtime, user, cursor, limit)


@router.get("/api/queue/events")
async def queue_events(
        request: Request,
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME):
    '''Stream queue changes scoped to the authenticated owner.'''

    async def event_stream() -> AsyncIterator[str]:  # pylint: disable=too-many-branches
        subscriber_id, updates_queue = runtime.store.subscribe_owner(user)
        first = True
        loop = asyncio.get_running_loop()
        keepalive_at = loop.time() + SSE_KEEPALIVE_SECONDS
        try:
            while True:
                if await request.is_disconnected():
                    return
                if first:
                    yield "event: queue\ndata: " + json.dumps(
                        _queue_snapshot(runtime, user, limit=QUEUE_PAGE_SIZE),
                        separators=(",", ":")
                    ) + "\n\n"
                    first = False
                # Keep the queue wait short enough to notice a disconnected
                # client promptly, while sending SSE keepalives less often.
                try:
                    updates = await asyncio.wait_for(
                        updates_queue.get_batch(),
                        timeout=min(
                            SSE_DISCONNECT_POLL_SECONDS,
                            max(0.0, keepalive_at - loop.time()),
                        ),
                    )
                except asyncio.TimeoutError:
                    if loop.time() >= keepalive_at:
                        yield ": keepalive\n\n"
                        keepalive_at = loop.time() + SSE_KEEPALIVE_SECONDS
                    continue

                changed_ids = {
                    job_id for update_type, job_id in updates
                    if update_type != "job-removed"
                }
                if any(update_type == "queue-changed" for update_type, _ in updates):
                    changed_ids.update(runtime.runner.active_job_ids(user))
                positions = runtime.runner.pending_positions_for(changed_ids)
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
                        job = runtime.store.queue_snapshot(job_id)
                        if job is None:
                            continue
                        payload = queue_payload(job, positions)
                    yield "event: " + update_type + "\ndata: " + json.dumps(
                        payload, separators=(",", ":")
                    ) + "\n\n"
                yield "event: queue-meta\ndata: " + json.dumps(
                    _queue_meta(runtime, user), separators=(",", ":")
                ) + "\n\n"
        finally:
            runtime.store.unsubscribe_owner(user, subscriber_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/jobs/{job_id}/details", response_model=JobResponse)
async def job_details(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> dict[str, Any]:
    '''Return the detailed job state without its large validation reports.'''
    job = JobAccessService(runtime.store).require_snapshot(job_id, user)
    return JobSerializer.from_record(job)


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> dict[str, Any]:
    '''Request cancellation at the next safe pipeline boundary.'''
    job = JobAccessService(runtime.store).require_access(job_id, user)
    if job.is_terminal():
        raise HTTPException(
            status_code=409, detail=f"This job has already finished ({job.status})."
        )
    if not await asyncio.to_thread(runtime.runner.cancel, job_id):
        raise HTTPException(
            status_code=409, detail="This job finished before it could be cancelled."
        )
    return {"job_id": job_id, "status": str(job.status)}


@router.post("/api/jobs/{job_id}/retry", status_code=201)
async def retry_job(
        job_id: str = JOB_ID_PATH,
        skip_font_fix: bool = Form(True),
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> JSONResponse:
    '''Queue a retry using the saved original PDF.'''
    original = JobAccessService(runtime.store).require_access(
        job_id, user
    )
    if not original.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")
    if not original.input_path.is_file():
        raise HTTPException(
            status_code=409,
            detail="The original upload is no longer on disk; upload it again.",
        )
    runtime.assert_disk_space()
    job, jobs_ahead = await JobWorkflow(runtime.store, runtime.runner).create_retry(
        original, user, skip_font_fix, runtime.new_job_id
    )
    return JSONResponse(status_code=201, content={
        **JobSerializer.from_record(job), "jobs_ahead": jobs_ahead
    })


@router.delete("/api/jobs")
async def delete_jobs(
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> dict[str, Any]:
    '''Delete the caller's terminal jobs and retain active jobs.'''
    workflow = JobWorkflow(runtime.store, runtime.runner)
    jobs = runtime.store.list_access_snapshots(user)
    deleted: list[str] = []
    skipped: list[str] = []
    for job in jobs:
        if not job.is_terminal():
            skipped.append(job.job_id)
            continue
        await workflow.delete_terminal(job)
        deleted.append(job.job_id)
    return {"deleted": deleted, "skipped": skipped}


@router.delete("/api/jobs/{job_id}")
async def delete_job(
        job_id: str = JOB_ID_PATH,
        user: str = CURRENT_USER,
        runtime: ApiRuntime = CURRENT_RUNTIME) -> dict[str, Any]:
    '''Delete one of the caller's terminal jobs.'''
    job = JobAccessService(runtime.store).require_access(job_id, user)
    if not job.is_terminal():
        raise HTTPException(status_code=409, detail="The job is still running.")
    await JobWorkflow(runtime.store, runtime.runner).delete_terminal(job)
    return {"job_id": job_id, "deleted": True}
