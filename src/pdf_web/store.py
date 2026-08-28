'''
Thread-safe registry of remediation jobs and their event streams.
'''

from __future__ import annotations

import json
import re
import shutil
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import JOBS_ROOT, LOG_RING_BUFFER_LINES, job_ttl_hours
from .identity import legacy_job_owner, normalize_user
from .models import FileResult, Job, JobStatus, StepState, UploadedFile

JOB_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
PROGRESS_LINE_PATTERN = re.compile(r"^\s*\d+%\|")


class JobStore:
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

    def add(self, job: Job) -> None:
        '''
        Register a new job.
        '''
        with self._lock:
            self._jobs[job.job_id] = job
            self._events[job.job_id] = []
            self._order.append(job.job_id)

    def get(self, job_id: str) -> Job | None:
        '''
        Return a job by identifier.
        '''
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        '''
        Return all known jobs, newest first.
        '''
        with self._lock:
            return [self._jobs[job_id] for job_id in reversed(self._order)]

    def remove(self, job_id: str) -> Job | None:
        '''
        Drop a job from the registry.
        '''
        with self._lock:
            job = self._jobs.pop(job_id, None)
            self._events.pop(job_id, None)
            if job_id in self._order:
                self._order.remove(job_id)
            return job

    def emit(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        '''
        Append one event to a job's stream.
        '''
        with self._lock:
            events = self._events.get(job_id)
            if events is None:
                return
            events.append({
                "cursor": len(events) + 1,
                "type": event_type,
                "payload": payload,
            })

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
                return
            events.append({
                "cursor": len(events) + 1,
                "type": "log",
                "payload": {"line": line},
            })
            if len(events) > LOG_RING_BUFFER_LINES * 2:
                del events[:LOG_RING_BUFFER_LINES]

    def events_since(self, job_id: str, cursor: int) -> tuple[int, list[dict[str, Any]]]:
        '''
        Return events recorded after the given cursor.
        '''
        with self._lock:
            events = self._events.get(job_id, [])
            pending = [event for event in events if event["cursor"] > cursor]
            latest = events[-1]["cursor"] if events else cursor
            return latest, pending


def is_valid_job_id(job_id: str) -> bool:
    '''
    Return whether a string is a well-formed job identifier.
    '''
    return bool(JOB_ID_PATTERN.match(job_id or ""))


def save_meta(job: Job) -> None:
    '''
    Persist a job's metadata so downloads survive a server restart.
    '''
    job.web_path.mkdir(parents=True, exist_ok=True)
    payload = job.to_dict()
    payload["final_pdf_paths"] = {
        result.file_id: (
            result.final_pdf_path.relative_to(job.base_path).as_posix()
            if result.final_pdf_path else None
        )
        for result in job.results
    }
    job.meta_path.write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8"
    )


def load_meta(meta_path: Path) -> Job | None:
    '''
    Rebuild a job from persisted metadata.
    '''
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    job_id = payload.get("job_id", "")
    if not is_valid_job_id(job_id):
        return None

    job = Job(
        job_id=job_id,
        created_at=_parse_datetime(payload.get("created_at")),
        config_file=payload.get("config_file", ""),
        submitted_by=normalize_user(payload.get("submitted_by")) or (legacy_job_owner() or ""),
        skip_font_fix=bool(payload.get("skip_font_fix")),
        wcag_and_ua1_must_pass=bool(payload.get("wcag_and_ua1_must_pass")),
        verbose=bool(payload.get("verbose")),
        status=_parse_status(payload.get("status")),
        return_code=payload.get("return_code"),
        error=payload.get("error"),
        partial=bool(payload.get("partial")),
    )
    job.summary = payload.get("summary") or {}
    job.started_at = _parse_optional_datetime(payload.get("started_at"))
    job.finished_at = _parse_optional_datetime(payload.get("finished_at"))
    job.files = [
        UploadedFile(
            file_id=entry.get("file_id", ""),
            original_name=entry.get("original_name", ""),
            stored_name=entry.get("stored_name", ""),
            size_bytes=int(entry.get("size_bytes") or 0),
        )
        for entry in payload.get("files", [])
    ]
    for step in payload.get("steps", []):
        try:
            job.steps[int(step["number"])] = StepState(step["state"])
        except (KeyError, ValueError):
            continue

    final_paths = payload.get("final_pdf_paths", {})
    for entry in payload.get("results", []):
        file_id = entry.get("file_id", "")
        relative_path = final_paths.get(file_id)
        job.results.append(FileResult(
            file_id=file_id,
            outcome=entry.get("outcome", "unknown"),
            final_pdf_path=(job.base_path / relative_path) if relative_path else None,
            before=entry.get("before"),
            after=entry.get("after"),
            note=entry.get("note"),
        ))

    return job


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
