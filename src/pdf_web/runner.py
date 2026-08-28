'''
Run the remediation pipeline for queued jobs on a small worker pool.

The pipeline is a library call rather than a subprocess, so progress arrives as
structured stage events instead of being parsed out of console banners, and
cancellation is a flag the pipeline checks between stages.

Concurrency is capped twice: by machine capacity, and per user. A plain FIFO
queue cannot express the second cap, because when the job at the head belongs
to somebody already at their limit the next eligible job has to start without
that head job losing its place. Hence a list plus a condition variable.
'''

from __future__ import annotations

import threading
from collections import Counter
from datetime import datetime
from typing import Any

from pdf_api.models import PipelineOptions, PipelineStatus
from pdf_api.pipeline import process_pdf

from . import APP_NAME
from .config import max_concurrent_jobs, max_running_jobs_per_user
from .models import Job, JobStatus, status_for
from .store import JobStore, save_meta


class PipelineRunner:  # pylint: disable=too-many-instance-attributes
    '''
    Schedule pipeline runs across a pool of worker threads.
    '''

    def __init__(self, store: JobStore) -> None:
        '''
        Create a runner bound to a job store.
        '''
        self._store = store

        # One lock for all scheduler state. A Condition because a worker with
        # nothing eligible must block until another thread changes that, never
        # poll for it.
        self._condition = threading.Condition()
        self._pending: list[str] = []
        self._running: dict[str, str] = {}
        self._owners: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._stopping = False
        self._threads: list[threading.Thread] = []

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        '''
        Start the worker pool.
        '''
        if self._threads:
            return
        for index in range(max_concurrent_jobs()):
            thread = threading.Thread(
                target=self._worker_loop,
                name=f"pdf-web-runner-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        '''
        Stop every worker and ask running jobs to finish early.
        '''
        with self._condition:
            self._stopping = True
            self._cancelled.update(self._running)
            self._condition.notify_all()

        for thread in self._threads:
            thread.join(timeout=30.0)
        self._threads = []

    def is_running(self) -> bool:
        '''
        Return whether the pool can still take work.
        '''
        return not self._stopping and any(
            thread.is_alive() for thread in self._threads
        )

    def worker_health(self) -> tuple[int, int]:
        '''
        Return how many workers are alive out of how many were started.
        '''
        alive = sum(1 for thread in self._threads if thread.is_alive())
        return alive, len(self._threads)

    # -- submission and queue state -------------------------------------

    def submit(self, job_id: str, owner: str) -> int:
        '''
        Queue a job and return how many queued jobs are ahead of it.
        '''
        with self._condition:
            self._pending.append(job_id)
            self._owners[job_id] = owner
            position = len(self._pending) - 1
            self._condition.notify_all()
        return position

    def queue_depth(self) -> int:
        '''
        Return how many jobs are waiting.
        '''
        with self._condition:
            return len(self._pending)

    def running_count(self) -> int:
        '''
        Return how many jobs are executing.
        '''
        with self._condition:
            return len(self._running)

    def pending_job_ids(self) -> tuple[str, ...]:
        '''
        Return the queued jobs in the order they will be considered.
        '''
        with self._condition:
            return tuple(self._pending)

    def jobs_ahead(self, job_id: str) -> int | None:
        '''
        Return how many queued jobs precede this one, or None if not waiting.

        Running jobs are excluded: with a pool they no longer block a waiting
        job one for one, so counting them would overstate the wait.
        '''
        with self._condition:
            if job_id in self._running:
                return 0
            if job_id not in self._pending:
                return None
            return self._pending.index(job_id)

    def queue_status(self, job_id: str, owner: str) -> dict[str, Any]:
        '''
        Describe why a job is waiting, in terms its submitter can act on.
        '''
        with self._condition:
            limit = max_running_jobs_per_user()
            ahead = (
                self._pending.index(job_id) if job_id in self._pending else None
            )
            yours = sum(1 for value in self._running.values() if value == owner)
            return {
                "jobs_ahead": 0 if job_id in self._running else ahead,
                "your_running": yours,
                "your_limit": limit,
                "concurrency": max_concurrent_jobs(),
                "waiting_on_your_limit": ahead is not None and yours >= limit,
            }

    # -- cancellation ----------------------------------------------------

    def cancel(self, job_id: str) -> bool:
        '''
        Stop a queued or running job, returning whether anything was stopped.

        A queued job is finalized here, since nothing is executing it. A running
        job is flagged and the pipeline stops at its next stage boundary, so the
        partial result it produced is still recorded.
        '''
        with self._condition:
            if job_id in self._running:
                self._cancelled.add(job_id)
                self._condition.notify_all()
                return True
            if job_id in self._pending:
                self._pending.remove(job_id)
                self._owners.pop(job_id, None)
                self._condition.notify_all()
                queued = True
            else:
                return False

        if queued:
            job = self._store.get(job_id)
            if job is not None:
                self._finish_cancelled(job, "Cancelled before it started.")
            self._announce_queue_positions()
        return True

    def _is_cancelled(self, job_id: str) -> bool:
        '''
        Return whether a job has been asked to stop.
        '''
        with self._condition:
            return job_id in self._cancelled

    # -- scheduling ------------------------------------------------------

    def _claim_next(self) -> str | None:
        '''
        Take the first queued job whose owner is below the running cap.

        Scanning in order and taking the first eligible entry is what lets a
        capped user be skipped without their jobs losing their place.
        '''
        while True:
            with self._condition:
                if self._stopping:
                    return None

                limit = max_running_jobs_per_user()
                running_per_owner = Counter(self._running.values())

                for job_id in self._pending:
                    owner = self._owners.get(job_id, "")
                    if running_per_owner[owner] < limit:
                        self._pending.remove(job_id)
                        self._running[job_id] = owner
                        return job_id

                # Nothing queued, or everything queued belongs to somebody at
                # their cap. Both are resolved by another thread changing state,
                # never by time passing, so wait to be woken.
                self._condition.wait()

    def _release(self, job_id: str) -> None:
        '''
        Give back a job's slot and wake anyone its owner's cap was blocking.
        '''
        with self._condition:
            self._running.pop(job_id, None)
            self._owners.pop(job_id, None)
            self._cancelled.discard(job_id)
            self._condition.notify_all()

    def _worker_loop(self) -> None:
        '''
        Claim and run jobs until the runner stops.
        '''
        while True:
            job_id = self._claim_next()
            if job_id is None:
                return
            try:
                self._dispatch(job_id)
            except Exception as error:  # pylint: disable=broad-exception-caught
                # A worker that dies takes a slice of capacity with it and
                # nothing reports the loss, so no job may kill its thread.
                print(f"{APP_NAME}: worker error on {job_id}: {error}")
            finally:
                self._release(job_id)
                self._announce_queue_positions()

    def _dispatch(self, job_id: str) -> None:
        '''
        Run one claimed job, or finalize it if it can no longer run.
        '''
        job = self._store.get(job_id)
        if job is None or job.is_terminal():
            return
        if self._stopping or self._is_cancelled(job_id):
            self._finish_cancelled(job, "Cancelled before it started.")
            return
        self._run_job(job)

    # -- execution -------------------------------------------------------

    def _run_job(self, job: Job) -> None:
        '''
        Run the pipeline for one job and record what it produced.
        '''
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now()
        self._store.emit(job.job_id, "status", {"status": str(job.status)})
        self._log(job, f"Processing {job.file.original_name}")

        options = PipelineOptions(
            config_file=job.config_file,
            wcag_and_ua1_must_pass=job.wcag_and_ua1_must_pass,
            attempt_font_fix=not job.skip_font_fix,
        )

        result = process_pdf(
            job.input_path,
            job.output_dir,
            options,
            on_event=lambda stage: self._on_stage(job, stage),
            should_cancel=lambda: self._is_cancelled(job.job_id),
        )

        job.result = result
        job.outcome = str(result.status)
        job.status = status_for(result.status)
        job.error = result.error
        for warning in result.warnings:
            self._log(job, f"[WARN] {warning}")
        self._finish(job)

    def _on_stage(self, job: Job, stage) -> None:
        '''
        Record one stage as the pipeline finishes it.
        '''
        payload = stage.to_dict()
        job.stages.append(payload)
        self._log(
            job,
            f"{payload['name']}: {payload['status']}"
            + (f" - {payload['detail']}" if payload["detail"] else "")
        )
        self._store.emit(job.job_id, "stage", payload)

    def _finish_cancelled(self, job: Job, message: str) -> None:
        '''
        Mark a job cancelled without having run the pipeline.
        '''
        job.status = JobStatus.CANCELLED
        job.outcome = str(PipelineStatus.CANCELLED)
        job.error = message
        self._finish(job)

    def _finish(self, job: Job) -> None:
        '''
        Persist a finished job and announce it.
        '''
        job.finished_at = datetime.now()
        try:
            save_meta(job)
        except OSError as error:
            self._log(job, f"[ERROR] Could not persist job metadata: {error}")
        self._store.emit(job.job_id, "status", {"status": str(job.status)})
        self._store.emit(job.job_id, "done", {"status": str(job.status)})

    def _announce_queue_positions(self) -> None:
        '''
        Tell each waiting job its new position after the queue advances.
        '''
        with self._condition:
            positions = list(enumerate(self._pending))
        for index, pending_id in positions:
            self._store.emit(pending_id, "queue", {"jobs_ahead": index})

    def _log(self, job: Job, line: str) -> None:
        '''
        Record one line in the event stream and the on-disk log.
        '''
        self._store.append_log(job.job_id, line)
        try:
            job.log_path.parent.mkdir(parents=True, exist_ok=True)
            with job.log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        except OSError:
            pass
