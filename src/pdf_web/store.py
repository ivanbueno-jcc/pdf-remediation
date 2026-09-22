'''
Thread-safe registry of remediation jobs and owner update notifications.
'''

from __future__ import annotations

import copy
import asyncio
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from pdf_api.models import selected_validation_profiles
from pdf_api.pipeline import artifact_path

from .infrastructure.notifications import OwnerUpdateQueue
from .infrastructure.persistence import (
    is_valid_job_id,
    load_meta,
    load_persisted_jobs,
    save_meta,
    sweep_expired_jobs,
)
from .models import (
    Job,
    JobAccessSnapshot,
    JobStatus,
    QueueJobSnapshot,
    summarize_report,
)


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
        self.add_batch((job,))

    def add_batch(self, jobs: tuple[Job, ...], notify: bool = True) -> None:
        '''Register jobs together, optionally delaying owner notifications.'''
        notifications: list[
            tuple[
                str, tuple[str, str],
                list[tuple[int, asyncio.AbstractEventLoop, OwnerUpdateQueue]],
            ]
        ] = []
        with self._lock:
            for job in jobs:
                self._jobs[job.job_id] = job
                self._order.append(job.job_id)
                owner_ids = self._owner_order.setdefault(job.submitted_by, [])
                owner_positions = self._owner_positions.setdefault(job.submitted_by, {})
                owner_positions[job.job_id] = len(owner_ids)
                owner_ids.append(job.job_id)
                if notify:
                    update, subscribers = self._touch_owner_locked(
                        job.submitted_by, "job-added", job.job_id
                    )
                    notifications.append((job.submitted_by, update, subscribers))
        for owner, update, subscribers in notifications:
            self._publish_owner_update(owner, update, subscribers)

    def publish_job_added(self, job_ids: tuple[str, ...]) -> None:
        '''Publish additions after an external scheduler commit succeeds.'''
        notifications: list[
            tuple[
                str, tuple[str, str],
                list[tuple[int, asyncio.AbstractEventLoop, OwnerUpdateQueue]],
            ]
        ] = []
        with self._lock:
            for job_id in job_ids:
                job = self._jobs.get(job_id)
                if job is None:
                    continue
                update, subscribers = self._touch_owner_locked(
                    job.submitted_by, "job-added", job_id
                )
                notifications.append((job.submitted_by, update, subscribers))
        for owner, update, subscribers in notifications:
            self._publish_owner_update(owner, update, subscribers)

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
            snapshot.bundle_lock = threading.Lock()
            return snapshot

    @contextmanager
    def job_artifact_lock(self, job_id: str) -> Iterator[bool]:
        '''Serialize bundle creation and deletion for one live job.'''
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            yield False
            return
        with job.bundle_lock:
            yield True

    @staticmethod
    def _queue_snapshot(job: Job) -> QueueJobSnapshot:  # pylint: disable=too-many-locals
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
            job_id = job.job_id
            name = job.file.original_name
            page_count = job.file.page_count
            created_at = job.created_at
            started_at = job.started_at
            finished_at = job.finished_at
            config_file = job.config_file
            status = job.status
            outcome = job.outcome
            before = summarize_report(result.before if result else None)
            after = summarize_report(result.after if result else None)
            error = job.error
            output_dir = job.output_dir
            output_pdf_path = result.output_pdf_path if result else None
            stages_done = len(stages)
            current_stage = stages[-1]["name"] if stages else None

        # Filesystem checks can be slow and do not need the mutable job lock.
        has_pdf = artifact_path(output_dir, "pdf", output_pdf_path) is not None
        return QueueJobSnapshot(
            job_id=job_id,
            name=name,
            page_count=page_count,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            config_file=config_file,
            status=status,
            outcome=outcome,
            stages_done=stages_done,
            current_stage=current_stage,
            before=before,
            after=after,
            initially_secured=initially_secured,
            validation_requirement=validation_requirement,
            has_pdf=has_pdf,
            error=error,
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
        return self._access_snapshot(job) if job is not None else None

    @staticmethod
    def _access_snapshot(job: Job) -> JobAccessSnapshot:
        '''Copy only scalar state and paths needed by file operations.'''
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

    def list_access_snapshots(self, owner: str) -> list[JobAccessSnapshot]:
        '''Return compact access projections for one owner, newest first.'''
        with self._lock:
            jobs = [
                self._jobs[job_id]
                for job_id in reversed(self._owner_order.get(owner, []))
            ]
        return [self._access_snapshot(job) for job in jobs]

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

    def remove(self, job_id: str, notify: bool = True) -> Job | None:
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
                if notify:
                    update, subscribers = self._touch_owner_locked(
                        job.submitted_by, "job-removed", job.job_id
                    )
                else:
                    update = None
                    subscribers = []
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
