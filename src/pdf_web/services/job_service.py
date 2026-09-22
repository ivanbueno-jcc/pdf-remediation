'''Coordinate job lifecycle changes across storage, the runner, and disk.'''

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from typing import Callable

from fastapi import HTTPException

from ..models import Job, JobAccessSnapshot, UploadedFile
from ..runner import PipelineRunner
from ..store import JobStore, save_meta
from ..store import is_valid_job_id


class JobAccessService:
    '''Enforce owner scope and constrain job artifact paths to storage root.'''

    def __init__(self, store: JobStore, jobs_root) -> None:
        self._store = store
        self._jobs_root = jobs_root

    def require_access(self, job_id: str, owner: str) -> JobAccessSnapshot:
        '''Return an owned job or the same 404 used for an unknown identifier.'''
        if not is_valid_job_id(job_id):
            raise HTTPException(status_code=404, detail="Unknown job.")
        job = self._store.access_snapshot(job_id)
        if job is None or job.submitted_by != owner:
            raise HTTPException(status_code=404, detail="Unknown job.")
        return job

    def require_snapshot(self, job_id: str, owner: str) -> Job:
        '''Return a detached full job snapshot only to its owner.'''
        if not is_valid_job_id(job_id):
            raise HTTPException(status_code=404, detail="Unknown job.")
        job = self._store.snapshot(job_id)
        if job is None or job.submitted_by != owner:
            raise HTTPException(status_code=404, detail="Unknown job.")
        return job

    def require_file(self, candidate):
        '''Return an existing artifact path contained below the jobs root.'''
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._jobs_root.resolve()) or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Not found.")
        return resolved


class JobWorkflow:
    '''Own multi-step job workflows that span HTTP, storage, and execution.'''

    def __init__(self, store: JobStore, runner: PipelineRunner) -> None:
        self._store = store
        self._runner = runner

    async def commit_submission(self, jobs: list[Job], owner: str) -> list[int]:
        '''Persist and queue a prepared batch as one visible operation.'''
        registered_ids: tuple[str, ...] = ()
        try:
            for job in jobs:
                save_meta(job)

            job_ids = tuple(job.job_id for job in jobs)
            self._store.add_batch(tuple(jobs), notify=False)
            registered_ids = job_ids
            positions = self._runner.submit_batch(job_ids, owner)
            self._store.publish_job_added(job_ids)
            return positions
        except Exception:
            for job_id in registered_ids:
                self._runner.cancel(job_id)
            for job_id in registered_ids:
                self._store.remove(job_id, notify=False)
            await self.discard_prepared(jobs)
            raise

    async def create_retry(
            self,
            original: Job | JobAccessSnapshot,
            owner: str,
            skip_font_fix: bool,
            new_job_id: Callable[[set[str]], str],
    ) -> tuple[Job, int]:
        '''Copy a finished job's original input and queue a retry.'''
        profiles = original.required_profiles()
        job = Job(
            job_id=new_job_id(set()),
            created_at=datetime.now(),
            config_file=original.config_file,
            file=UploadedFile(
                original.original_name,
                original.stored_name,
                original.size_bytes,
            ),
            submitted_by=owner,
            attempt_unlock=original.attempt_unlock,
            attempt_fix=original.attempt_fix,
            skip_font_fix=skip_font_fix,
            attempt_targeted_fixes=original.attempt_targeted_fixes,
            require_wcag="wcag" in profiles,
            require_pdfua1="ua1" in profiles,
            verbose=original.verbose,
        )
        try:
            job.input_path.parent.mkdir(parents=True, exist_ok=True)
            job.web_path.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, original.input_path, job.input_path)
            save_meta(job)
            self._store.add_batch((job,), notify=False)
            positions = self._runner.submit(job.job_id, owner)
            self._store.publish_job_added((job.job_id,))
            return job, positions
        except Exception:
            self._runner.cancel(job.job_id)
            self._store.remove(job.job_id, notify=False)
            await self.discard_prepared([job])
            raise

    async def delete_terminal(self, job: Job | JobAccessSnapshot) -> None:
        '''Remove one terminal job while excluding concurrent artifact access.'''
        with self._store.job_artifact_lock(job.job_id):
            self._store.remove(job.job_id)
            await asyncio.to_thread(shutil.rmtree, job.base_path, True)

    async def discard_prepared(self, jobs: list[Job]) -> None:
        '''Remove uncommitted job directories after a workflow failure.'''
        if jobs:
            await asyncio.gather(*(
                asyncio.to_thread(shutil.rmtree, job.base_path, True)
                for job in jobs
            ))
