'''
Thread-safe registry of remediation jobs and owner update notifications.
'''

from __future__ import annotations

import copy
import asyncio
import json
import re
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pdf_api.models import PipelineResult, PipelineStatus, selected_validation_profiles
from pdf_api.pipeline import artifact_path

from .config import JOBS_ROOT, job_ttl_hours
from .identity import legacy_job_owner, normalize_user
from .models import (
    Job,
    JobAccessSnapshot,
    JobStatus,
    QueueJobSnapshot,
    UploadedFile,
    summarize_report,
)

JOB_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


class OwnerUpdateQueue:
    '''Bounded, event-loop-owned coalescing queue for one owner stream.'''

    def __init__(self) -> None:
        self._wake = asyncio.Queue(maxsize=1)
        self._pending: dict[str, tuple[str, str]] = {}

    def publish(self, update: tuple[str, str]) -> None:
        '''Merge one update and wake the consumer once.'''
        update_type, job_id = update
        previous = self._pending.get(job_id)
        if previous is None or update_type == "job-removed":
            self._pending[job_id] = update
        elif previous[1] != "job-removed":
            priority = {"job-updated": 1, "job-added": 2, "queue-changed": 3}
            if priority.get(update_type, 0) >= priority.get(previous[1], 0):
                self._pending[job_id] = update
        if self._wake.empty():
            self._wake.put_nowait(None)

    async def get_batch(self) -> list[tuple[str, str]]:
        '''Wait for updates and return the coalesced batch.'''
        await self._wake.get()
        updates = list(self._pending.values())
        self._pending.clear()
        return updates


class JobStore:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    '''
    Hold jobs in memory and persist completed metadata.
    '''

    def __init__(self) -> None:
        '''
        Create an empty store.
        '''
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._owner_order: dict[str, list[str]] = {}
        self._owner_positions: dict[str, dict[str, int]] = {}
        self._owner_subscribers: dict[
            str, dict[int, tuple[asyncio.AbstractEventLoop, OwnerUpdateQueue]]
        ] = {}
        self._next_subscriber_id = 0

    def _touch_owner_locked(
            self, owner: str, update_type: str, job_id: str
    ) -> tuple[tuple[str, str], list[tuple[int, asyncio.AbstractEventLoop, OwnerUpdateQueue]]]:
        '''Record an update and copy subscriber targets while locked.'''
        subscribers = [
            (subscriber_id, loop, queue)
            for subscriber_id, (loop, queue)
            in self._owner_subscribers.get(owner, {}).items()
        ]
        return (update_type, job_id), subscribers

    def _publish_owner_update(
            self,
            owner: str,
            update: tuple[str, str],
            subscribers: list[tuple[int, asyncio.AbstractEventLoop, OwnerUpdateQueue]],
    ) -> None:
        '''Publish outside the store lock and discard closed subscribers.'''
        stale: list[int] = []
        for subscriber_id, loop, queue in subscribers:
            if loop.is_closed():
                stale.append(subscriber_id)
                continue
            try:
                loop.call_soon_threadsafe(queue.publish, update)
            except RuntimeError:
                stale.append(subscriber_id)
        if stale:
            with self._lock:
                subscribers_for_owner = self._owner_subscribers.get(owner, {})
                for subscriber_id in stale:
                    subscribers_for_owner.pop(subscriber_id, None)
                if not subscribers_for_owner:
                    self._owner_subscribers.pop(owner, None)

    def add(self, job: Job) -> None:
        '''
        Register a new job.
        '''
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            owner_ids = self._owner_order.setdefault(job.submitted_by, [])
            owner_positions = self._owner_positions.setdefault(job.submitted_by, {})
            owner_positions[job.job_id] = len(owner_ids)
            owner_ids.append(job.job_id)
            update, subscribers = self._touch_owner_locked(
                job.submitted_by, "job-added", job.job_id
            )
        self._publish_owner_update(job.submitted_by, update, subscribers)

    def get(self, job_id: str) -> Job | None:
        '''
        Return a detached snapshot by identifier.
        '''
        with self._lock:
            job = self._jobs.get(job_id)
        return self._snapshot(job) if job is not None else None

    def get_mutable(self, job_id: str) -> Job | None:
        '''Return the live job for internal runner/store mutation only.'''
        with self._lock:
            return self._jobs.get(job_id)

    @staticmethod
    def _snapshot(job: Job) -> Job:
        '''Copy a job consistently without holding the registry lock.'''
        with job.state_lock:
            snapshot = copy.copy(job)
            snapshot.file = copy.deepcopy(job.file)
            snapshot.stages = copy.deepcopy(job.stages)
            snapshot.result = copy.deepcopy(job.result)
            snapshot.state_lock = threading.RLock()
            return snapshot

    @staticmethod
    def _queue_snapshot(job: Job) -> QueueJobSnapshot:
        '''Copy only fields needed for one queue row.'''
        with job.state_lock:
            result = job.result
            stages = job.stages
            profiles = selected_validation_profiles(
                job.require_wcag, job.require_pdfua1, job.wcag_and_ua1_must_pass
            )
            if profiles == ("wcag",):
                validation_requirement = "wcag only"
            elif profiles == ("ua1",):
                validation_requirement = "pdfua1 only"
            elif profiles == ("wcag", "ua1"):
                validation_requirement = "wcag and pdfua1"
            else:
                validation_requirement = "no validation profile"
            if result is not None and result.initially_secured is not None:
                initially_secured = bool(result.initially_secured)
            else:
                initially_secured = any(
                    stage.get("name") == "unlock" and stage.get("status") == "ok"
                    for stage in stages
                )
            return QueueJobSnapshot(
                job_id=job.job_id,
                name=job.file.original_name,
                page_count=job.file.page_count,
                created_at=job.created_at,
                started_at=job.started_at,
                finished_at=job.finished_at,
                config_file=job.config_file,
                status=job.status,
                outcome=job.outcome,
                stages_done=len(stages),
                current_stage=stages[-1]["name"] if stages else None,
                before=summarize_report(result.before if result else None),
                after=summarize_report(result.after if result else None),
                initially_secured=initially_secured,
                validation_requirement=validation_requirement,
                has_pdf=artifact_path(
                    job.output_dir, "pdf",
                    result.output_pdf_path if result else None,
                ) is not None,
                error=job.error,
            )

    def queue_snapshot(self, job_id: str) -> QueueJobSnapshot | None:
        '''Return one compact queue projection.'''
        with self._lock:
            job = self._jobs.get(job_id)
        return self._queue_snapshot(job) if job is not None else None

    def list_queue_snapshots(self, owner: str) -> list[QueueJobSnapshot]:
        '''Return compact queue projections for one owner.'''
        with self._lock:
            jobs = [
                self._jobs[job_id]
                for job_id in reversed(self._owner_order.get(owner, []))
            ]
        return [self._queue_snapshot(job) for job in jobs]

    def snapshot(self, job_id: str) -> Job | None:
        '''Return one lock-consistent, detached job snapshot.'''
        return self.get(job_id)

    def access_snapshot(self, job_id: str) -> JobAccessSnapshot | None:
        '''Return scalar job state for authorization and file operations.'''
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        with job.state_lock:
            result = job.result
            return JobAccessSnapshot(
                job_id=job.job_id,
                submitted_by=job.submitted_by,
                status=job.status,
                original_name=job.file.original_name,
                stored_name=job.file.stored_name,
                size_bytes=job.file.size_bytes,
                config_file=job.config_file,
                attempt_unlock=job.attempt_unlock,
                attempt_fix=job.attempt_fix,
                skip_font_fix=job.skip_font_fix,
                attempt_targeted_fixes=job.attempt_targeted_fixes,
                require_wcag=job.require_wcag,
                require_pdfua1=job.require_pdfua1,
                verbose=job.verbose,
                output_pdf_path=result.output_pdf_path if result else None,
            )

    def list_snapshots(self, owner: str) -> list[Job]:
        '''Return detached snapshots for one owner, newest first.'''
        with self._lock:
            jobs = [
                self._jobs[job_id]
                for job_id in reversed(self._owner_order.get(owner, []))
            ]
        return [self._snapshot(job) for job in jobs]

    def list_jobs(self) -> list[Job]:
        '''
        Return all known jobs, newest first.
        '''
        with self._lock:
            jobs = [
                self._jobs[job_id]
                for job_id in reversed(self._order)
            ]
        return [self._snapshot(job) for job in jobs]

    def remove(self, job_id: str) -> Job | None:
        '''
        Drop a job from the registry.
        '''
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if job_id in self._order:
                self._order.remove(job_id)
            if job is not None:
                owner_jobs = self._owner_order.get(job.submitted_by, [])
                owner_positions = self._owner_positions.get(job.submitted_by, {})
                position = owner_positions.pop(job_id, None)
                if position is not None:
                    owner_jobs.pop(position)
                    for index in range(position, len(owner_jobs)):
                        owner_positions[owner_jobs[index]] = index
                if not owner_jobs:
                    self._owner_order.pop(job.submitted_by, None)
                    self._owner_positions.pop(job.submitted_by, None)
                update, subscribers = self._touch_owner_locked(
                    job.submitted_by, "job-removed", job.job_id
                )
            else:
                update = None
                subscribers = []
        if update is not None:
            self._publish_owner_update(job.submitted_by, update, subscribers)
        return job

    def active_job_ids(self, owner: str) -> tuple[str, ...]:
        '''Return queued or running IDs for an owner without copying results.'''
        with self._lock:
            active: list[str] = []
            for job_id in self._owner_order.get(owner, []):
                job = self._jobs[job_id]
                with job.state_lock:
                    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
                        active.append(job_id)
            return tuple(active)

    def owner_job_count(self, owner: str) -> int:
        '''Return the number of jobs owned by a user.'''
        with self._lock:
            return len(self._owner_order.get(owner, []))

    def subscribe_owner(
            self, owner: str
    ) -> tuple[int, OwnerUpdateQueue]:
        '''Register an async subscriber for owner-scoped live updates.'''
        queue = OwnerUpdateQueue()
        loop = asyncio.get_running_loop()
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._owner_subscribers.setdefault(owner, {})[subscriber_id] = (
                loop, queue
            )
        return subscriber_id, queue

    def unsubscribe_owner(self, owner: str, subscriber_id: int) -> None:
        '''Remove an async owner subscriber.'''
        with self._lock:
            subscribers = self._owner_subscribers.get(owner)
            if subscribers is None:
                return
            subscribers.pop(subscriber_id, None)
            if not subscribers:
                self._owner_subscribers.pop(owner, None)

    def list_jobs_for_user(
            self,
            user: str,
            cursor: str | None = None,
            limit: int | None = None) -> tuple[list[QueueJobSnapshot], int, str | None]:
        '''Return one newest-first page without scanning other users' jobs.'''
        with self._lock:
            owner_ids = self._owner_order.get(user, [])
            total = len(owner_ids)
            if cursor is None:
                position = total - 1
            else:
                cursor_position = self._owner_positions.get(user, {}).get(cursor)
                if cursor_position is None:
                    raise ValueError("Invalid queue cursor.")
                position = cursor_position - 1

            page_ids: list[str] = []
            while position >= 0 and (limit is None or len(page_ids) < limit):
                page_ids.append(owner_ids[position])
                position -= 1
            jobs = [self._jobs[job_id] for job_id in page_ids]
            next_cursor = page_ids[-1] if position >= 0 else None
        return [self._queue_snapshot(job) for job in jobs], total, next_cursor

    def emit(self, job_id: str, event_type: str, _payload: dict[str, Any]) -> None:
        '''
        Publish a state change to owner-scoped subscribers.
        '''
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            update_type = (
                "queue-changed" if event_type == "status" else "job-updated"
            )
            update, subscribers = self._touch_owner_locked(
                job.submitted_by, update_type, job_id
            )
        self._publish_owner_update(job.submitted_by, update, subscribers)

def is_valid_job_id(job_id: str) -> bool:
    '''
    Return whether a string is a well-formed job identifier.
    '''
    return bool(JOB_ID_PATTERN.match(job_id or ""))


def save_meta(job: Job) -> None:
    """
    Persist a job's metadata so downloads survive a server restart.
    """
    with job.state_lock:
        job.web_path.mkdir(parents=True, exist_ok=True)
        payload = job.to_dict()
        result = job.result
        payload["output_pdf_path"] = (
            result.output_pdf_path.relative_to(job.base_path).as_posix()
            if result and result.output_pdf_path
            and result.output_pdf_path.is_relative_to(job.base_path)
            else None
        )
        job.meta_path.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )


def load_meta(meta_path: Path) -> Job | None:
    """
    Rebuild a job from persisted metadata.

    Metadata written before one-PDF-per-job carries a list of files and cannot
    honestly be represented as a single-file job, so it is skipped rather than
    guessed at.
    """
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    job_id = payload.get("job_id", "")
    if not is_valid_job_id(job_id):
        return None

    file_payload = payload.get("file")
    if not isinstance(file_payload, dict):
        return None

    legacy_both = bool(payload.get("wcag_and_ua1_must_pass"))
    if "require_wcag" in payload or "require_pdfua1" in payload:
        require_wcag = bool(payload.get("require_wcag"))
        require_pdfua1 = bool(payload.get("require_pdfua1"))
    else:
        require_wcag = True
        require_pdfua1 = legacy_both

    job = Job(
        job_id=job_id,
        created_at=_parse_datetime(payload.get("created_at")),
        config_file=payload.get("config_file", ""),
        file=UploadedFile(
            original_name=file_payload.get("original_name", ""),
            stored_name=file_payload.get("stored_name", ""),
            size_bytes=int(file_payload.get("size_bytes") or 0),
            page_count=(
                int(file_payload["page_count"])
                if file_payload.get("page_count") is not None else None
            ),
        ),
        submitted_by=(
            normalize_user(payload.get("submitted_by")) or (legacy_job_owner() or "")
        ),
        attempt_unlock=bool(payload.get("attempt_unlock", True)),
        attempt_fix=bool(payload.get("attempt_fix", True)),
        skip_font_fix=bool(payload.get("skip_font_fix")),
        attempt_targeted_fixes=bool(payload.get("attempt_targeted_fixes", True)),
        require_wcag=require_wcag,
        require_pdfua1=require_pdfua1,
        verbose=bool(payload.get("verbose")),
        status=_parse_status(payload.get("status")),
        outcome=payload.get("outcome"),
        error=payload.get("error"),
    )
    job.started_at = _parse_optional_datetime(payload.get("started_at"))
    job.finished_at = _parse_optional_datetime(payload.get("finished_at"))
    job.stages = list(payload.get("stages") or [])

    output_relative = payload.get("output_pdf_path")
    job.result = PipelineResult(
        status=_parse_pipeline_status(payload.get("outcome")),
        input_pdf_path=job.input_path,
        output_pdf_path=(
            job.base_path / output_relative if output_relative else None
        ),
        before=_read_report(job.output_dir / "before.json"),
        after=_read_report(job.output_dir / "after.json"),
        initially_secured=(
            payload.get("initially_secured")
            if isinstance(payload.get("initially_secured"), bool) else None
        ),
        warnings=list(payload.get("warnings") or []),
        diagnostics=list(payload.get("diagnostics") or []),
        error=payload.get("error"),
    )
    return job


def _read_report(path: Path) -> dict | None:
    """
    Read a stored validation report back from disk.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_pipeline_status(value: object) -> PipelineStatus:
    """
    Parse a persisted pipeline outcome, defaulting to failed.
    """
    try:
        return PipelineStatus(str(value))
    except ValueError:
        return PipelineStatus.FAILED


def load_persisted_jobs(store: JobStore) -> tuple[int, int]:
    '''
    Load previously completed jobs from disk into the store.

    Returns the number loaded and the number that have no owner. Unowned jobs
    predate ownership and are unreachable by every user, so the count is
    reported rather than left as a silent surprise.
    '''
    if not JOBS_ROOT.is_dir():
        return 0, 0

    loaded = 0
    unowned = 0
    for job_path in sorted(JOBS_ROOT.iterdir()):
        if not job_path.is_dir() or not is_valid_job_id(job_path.name):
            continue
        meta_path = job_path / "_web" / "meta.json"
        if not meta_path.is_file():
            continue
        job = load_meta(meta_path)
        if job is None:
            continue
        if not job.is_terminal():
            job.status = JobStatus.FAILED
            job.error = job.error or "Server restarted while this job was running."
        if not job.submitted_by:
            unowned += 1
        store.add(job)
        loaded += 1
    return loaded, unowned


def sweep_expired_jobs(store: JobStore) -> int:
    '''
    Delete job directories older than the retention window.
    '''
    ttl_hours = job_ttl_hours()
    if ttl_hours <= 0 or not JOBS_ROOT.is_dir():
        return 0

    cutoff = datetime.now() - timedelta(hours=ttl_hours)
    removed = 0
    for job_path in sorted(JOBS_ROOT.iterdir()):
        if not job_path.is_dir() or not is_valid_job_id(job_path.name):
            continue
        job = store.get(job_path.name)
        if job is not None and not job.is_terminal():
            continue
        if datetime.fromtimestamp(job_path.stat().st_mtime) > cutoff:
            continue
        shutil.rmtree(job_path, ignore_errors=True)
        store.remove(job_path.name)
        removed += 1
    return removed


def _parse_status(value: object) -> JobStatus:
    '''
    Parse a persisted job status, defaulting to failed.
    '''
    try:
        return JobStatus(str(value))
    except ValueError:
        return JobStatus.FAILED


def _parse_datetime(value: object) -> datetime:
    '''
    Parse a persisted timestamp, defaulting to now.
    '''
    return _parse_optional_datetime(value) or datetime.now()


def _parse_optional_datetime(value: object) -> datetime | None:
    '''
    Parse a persisted timestamp that may be absent.
    '''
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
