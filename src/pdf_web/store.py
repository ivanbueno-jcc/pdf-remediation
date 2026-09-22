'''
Thread-safe registry of remediation jobs and their event streams.
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

from pdf_api.models import PipelineResult, PipelineStatus

from .config import JOBS_ROOT, LOG_RING_BUFFER_LINES, job_ttl_hours
from .identity import legacy_job_owner, normalize_user
from .models import Job, JobStatus, TERMINAL_STATUSES, UploadedFile

JOB_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
PROGRESS_LINE_PATTERN = re.compile(r"^\s*\d+%\|")


class JobStore:  # pylint: disable=too-many-instance-attributes
    '''
    Hold jobs in memory, record their events, and persist completed metadata.
    '''

    def __init__(self) -> None:
        '''
        Create an empty store.
        '''
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._order: list[str] = []
        self._owner_order: dict[str, list[str]] = {}
        self._owner_positions: dict[str, dict[str, int]] = {}
        self._owner_versions: dict[str, int] = {}
        self._owner_updates: dict[str, list[tuple[int, str, str]]] = {}
        self._owner_subscribers: dict[
            str, dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]]
        ] = {}
        self._next_subscriber_id = 0
        self._condition = threading.Condition(self._lock)

    def _touch_owner_locked(
            self, owner: str, update_type: str, job_id: str) -> None:
        '''Record an owner's live update and wake its stream.'''
        version = self._owner_versions.get(owner, 0) + 1
        self._owner_versions[owner] = version
        updates = self._owner_updates.setdefault(owner, [])
        updates.append((version, update_type, job_id))
        if len(updates) > 1000:
            del updates[:-1000]
        self._condition.notify_all()
        for loop, queue in self._owner_subscribers.get(owner, {}).values():
            loop.call_soon_threadsafe(queue.put_nowait, (version, update_type, job_id))

    def add(self, job: Job) -> None:
        '''
        Register a new job.
        '''
        with self._lock:
            self._jobs[job.job_id] = job
            self._events[job.job_id] = []
            self._order.append(job.job_id)
            owner_ids = self._owner_order.setdefault(job.submitted_by, [])
            owner_positions = self._owner_positions.setdefault(job.submitted_by, {})
            owner_positions[job.job_id] = len(owner_ids)
            owner_ids.append(job.job_id)
            self._touch_owner_locked(job.submitted_by, "job-added", job.job_id)

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

    def snapshot(self, job_id: str) -> Job | None:
        '''Return one lock-consistent, detached job snapshot.'''
        return self.get(job_id)

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
            self._events.pop(job_id, None)
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
                self._touch_owner_locked(job.submitted_by, "job-removed", job.job_id)
            return job

    def owner_version(self, owner: str) -> int:
        '''Return the owner's current live-update version.'''
        with self._lock:
            return self._owner_versions.get(owner, 0)

    def wait_for_owner_change(
            self, owner: str, version: int, timeout: float
    ) -> tuple[int, list[tuple[int, str, str]]]:
        '''Wait until an owner changes, returning its latest version and updates.'''
        with self._condition:
            self._condition.wait_for(
                lambda: self._owner_versions.get(owner, 0) != version,
                timeout=timeout,
            )
            latest = self._owner_versions.get(owner, 0)
            return latest, self._owner_updates_since_locked(owner, version)

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

    def _owner_updates_since_locked(
            self, owner: str, version: int) -> list[tuple[int, str, str]]:
        '''Return retained owner updates after a version.'''
        return [
            update for update in self._owner_updates.get(owner, [])
            if update[0] > version
        ]

    def owner_updates_since(
            self, owner: str, version: int) -> list[tuple[int, str, str]]:
        '''Return retained live updates after a version.'''
        with self._lock:
            return self._owner_updates_since_locked(owner, version)

    def owner_job_count(self, owner: str) -> int:
        '''Return the number of jobs owned by a user.'''
        with self._lock:
            return len(self._owner_order.get(owner, []))

    def subscribe_owner(
            self, owner: str
    ) -> tuple[int, asyncio.Queue[tuple[int, str, str]]]:
        '''Register an async subscriber for owner-scoped live updates.'''
        queue: asyncio.Queue[tuple[int, str, str]] = asyncio.Queue()
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
            limit: int | None = None) -> tuple[list[Job], int, str | None]:
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
        return [self._snapshot(job) for job in jobs], total, next_cursor

    def emit(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        '''
        Append one event to a job's stream.
        '''
        with self._lock:
            events = self._events.get(job_id)
            if events is None:
                return
            job = self._jobs.get(job_id)
            if job is None:
                return
            events.append({
                "cursor": len(events) + 1,
                "type": event_type,
                "payload": payload,
            })
            update_type = (
                "queue-changed" if event_type == "status" else "job-updated"
            )
            self._touch_owner_locked(job.submitted_by, update_type, job_id)

    def append_log(self, job_id: str, line: str) -> None:
        '''
        Append one output line, collapsing consecutive progress-bar redraws.
        '''
        with self._lock:
            events = self._events.get(job_id)
            if events is None:
                return
            if (
                events
                and events[-1]["type"] == "log"
                and PROGRESS_LINE_PATTERN.match(line)
                and PROGRESS_LINE_PATTERN.match(events[-1]["payload"].get("line", ""))
            ):
                events[-1]["payload"]["line"] = line
                self._condition.notify_all()
                return
            events.append({
                "cursor": len(events) + 1,
                "type": "log",
                "payload": {"line": line},
            })
            if len(events) > LOG_RING_BUFFER_LINES * 2:
                del events[:LOG_RING_BUFFER_LINES]
            self._condition.notify_all()

    def events_since(self, job_id: str, cursor: int) -> tuple[int, list[dict[str, Any]]]:
        '''
        Return events recorded after the given cursor.
        '''
        with self._lock:
            events = self._events.get(job_id, [])
            pending = [event for event in events if event["cursor"] > cursor]
            latest = events[-1]["cursor"] if events else cursor
            return latest, pending

    def wait_for_job_events(
            self, job_id: str, cursor: int, timeout: float
    ) -> tuple[int, list[dict[str, Any]], bool, bool]:
        '''Wait for job events, removal, terminal state, or a keepalive timeout.'''
        with self._condition:
            def changed() -> bool:
                events = self._events.get(job_id)
                return (
                    events is None
                    or (events and events[-1]["cursor"] > cursor)
                )

            self._condition.wait_for(changed, timeout=timeout)
            events = self._events.get(job_id)
            if events is None:
                return cursor, [], False, False
            pending = [event for event in events if event["cursor"] > cursor]
            latest = events[-1]["cursor"] if events else cursor
            job = self._jobs.get(job_id)
            terminal = False
            if job is not None:
                with job.state_lock:
                    terminal = job.status in TERMINAL_STATUSES
            return latest, pending, True, terminal


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
