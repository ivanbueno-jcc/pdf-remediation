'''
A small job registry so the API can accept a PDF and answer for it later.

Deliberately minimal: no authentication, no ownership, no persistence. The
pipeline takes minutes, so the HTTP surface has to be asynchronous, but this is
a programmatic service rather than a second web application. pdf_web keeps the
richer job model and calls the pipeline library directly.
'''

from __future__ import annotations

import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import PipelineOptions, PipelineResult, StageOutcome
from .pipeline import process_pdf

JOB_ID_LENGTH = 32


class JobState(StrEnum):
    '''
    Lifecycle of one submitted PDF.
    '''

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class Job:  # pylint: disable=too-many-instance-attributes
    '''
    One submitted PDF and whatever is known about it so far.
    '''

    job_id: str
    original_name: str
    input_path: Path
    output_dir: Path
    options: PipelineOptions
    state: JobState = JobState.QUEUED
    created_at: datetime = field(default_factory=datetime.now)
    stages: list[StageOutcome] = field(default_factory=list)
    result: PipelineResult | None = None
    error: str | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        '''
        Return a JSON-serializable view.
        '''
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "original_name": self.original_name,
            "state": str(self.state),
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "stages": [stage.to_dict() for stage in self.stages],
            "error": self.error,
        }
        if self.result is not None:
            payload["result"] = self.result.to_dict()
        return payload

    def artifact(self, name: str) -> Path | None:
        '''
        Return the path of one downloadable artifact, if it exists.
        '''
        if self.result is None:
            return None
        candidates = {
            "pdf": self.result.output_pdf_path,
            "before": self.output_dir / "before.json",
            "after": self.output_dir / "after.json",
        }
        candidate = candidates.get(name)
        if candidate is None or not Path(candidate).is_file():
            return None
        return Path(candidate)


def max_concurrent_jobs() -> int:
    '''
    Return how many PDFs may be processed at once.

    Each run holds roughly one veraPDF JVM and one PDFix process, so this is a
    machine-capacity number. Kept low by default until the PDFix licence has
    been verified at width.
    '''
    raw = os.getenv("PDF_API_MAX_CONCURRENT_JOBS", "").strip()
    try:
        return max(1, int(raw)) if raw else 2
    except ValueError:
        return 2


class JobRegistry:
    '''
    Hold submitted jobs and run them on a small worker pool.
    '''

    def __init__(self, workspace: Path, workers: int | None = None) -> None:
        '''
        Create a registry storing uploads and outputs under one directory.
        '''
        self._workspace = workspace
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._executor = ThreadPoolExecutor(
            max_workers=workers or max_concurrent_jobs(),
            thread_name_prefix="pdf-api-worker",
        )

    def submit(self, original_name: str, data_path: Path,
               options: PipelineOptions) -> Job:
        '''
        Register a PDF and queue it for processing.
        '''
        job_id = uuid4().hex[:JOB_ID_LENGTH]
        output_dir = self._workspace / job_id / "out"
        output_dir.mkdir(parents=True, exist_ok=True)

        job = Job(
            job_id=job_id,
            original_name=original_name,
            input_path=data_path,
            output_dir=output_dir,
            options=options,
        )
        with self._lock:
            self._jobs[job_id] = job
            self._order.append(job_id)

        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        '''
        Return a job by identifier.
        '''
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        '''
        Return every job, newest first.
        '''
        with self._lock:
            return [self._jobs[job_id] for job_id in reversed(self._order)]

    def cancel(self, job_id: str) -> bool:
        '''
        Ask a job to stop at its next stage boundary.
        '''
        job = self.get(job_id)
        if job is None or job.state in {JobState.DONE, JobState.ERROR, JobState.CANCELLED}:
            return False
        job.cancel_requested = True
        return True

    def remove(self, job_id: str) -> bool:
        '''
        Forget a job and delete everything it produced.
        '''
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if job_id in self._order:
                self._order.remove(job_id)
        if job is None:
            return False
        shutil.rmtree(self._workspace / job_id, ignore_errors=True)
        return True

    def shutdown(self) -> None:
        '''
        Stop accepting work and ask running jobs to finish early.
        '''
        for job in self.list_jobs():
            job.cancel_requested = True
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job: Job) -> None:
        '''
        Execute one job on a worker thread.
        '''
        job.state = JobState.RUNNING
        try:
            result = process_pdf(
                job.input_path,
                job.output_dir,
                job.options,
                on_event=job.stages.append,
                should_cancel=lambda: job.cancel_requested,
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            job.state = JobState.ERROR
            job.error = f"{type(error).__name__}: {error}"
            return

        job.result = result
        _write_reports(job.output_dir, result)

        if str(result.status) == "cancelled":
            job.state = JobState.CANCELLED
        elif result.succeeded():
            job.state = JobState.DONE
        else:
            job.state = JobState.ERROR
            job.error = result.error


def _write_reports(output_dir: Path, result: PipelineResult) -> None:
    '''
    Write the before and after reports next to the remediated PDF.
    '''
    for name, report in (("before.json", result.before), ("after.json", result.after)):
        path = output_dir / name
        if report is None:
            path.unlink(missing_ok=True)
            continue
        path.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
