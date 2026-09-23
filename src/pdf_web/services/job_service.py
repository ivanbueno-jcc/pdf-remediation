'''Coordinate job lifecycle changes across storage, the runner, and disk.'''

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from typing import Callable

from fastapi import HTTPException

from ..infrastructure.persistence import delete_persisted_job
from ..models import JobAccessSnapshot, JobPaths, JobRecord, JobSpec, JobState, UploadedFile
from ..runner import PipelineRunner
from ..store import JobStore, save_meta
from ..store import is_valid_job_id


class JobAccessService:
    '''Enforce owner scope for job lifecycle and artifact access.'''

    def __init__(self, store: JobStore) -> None:
        self._store = store

    def require_access(self, job_id: str, owner: str) -> JobAccessSnapshot:
        '''Return an owned job or the same 404 used for an unknown identifier.'''
        if not is_valid_job_id(job_id):
            raise HTTPException(status_code=404, detail="Unknown job.")
        job = self._store.access_snapshot(job_id)
        if job is None or job.submitted_by != owner:
            raise HTTPException(status_code=404, detail="Unknown job.")
        return job

    def require_snapshot(self, job_id: str, owner: str) -> JobRecord:
        '''Return a detached full job snapshot only to its owner.'''
        if not is_valid_job_id(job_id):
            raise HTTPException(status_code=404, detail="Unknown job.")
        job = self._store.snapshot(job_id)
        if job is None or job.spec.submitted_by != owner:
            raise HTTPException(status_code=404, detail="Unknown job.")
        return job

class JobWorkflow:
    '''Own multi-step job workflows that span HTTP, storage, and execution.'''

    def __init__(self, store: JobStore, runner: PipelineRunner) -> None:
        self._store = store
        self._runner = runner

    async def commit_submission(self, jobs: list[JobRecord], owner: str) -> list[int]:
        '''Persist and queue a prepared batch as one visible operation.'''
        registered_ids: tuple[str, ...] = ()
        try:
            for job in jobs:
                save_meta(job)

            job_ids = tuple(job.spec.job_id for job in jobs)
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
            await asyncio.gather(*(
                asyncio.to_thread(delete_persisted_job, job.spec.job_id, job.paths.root)
                for job in jobs
            ))
            raise

    async def create_retry(
            self,
            original: JobRecord | JobAccessSnapshot,
            owner: str,
            skip_font_fix: bool,
            new_job_id: Callable[[set[str]], str],
    ) -> tuple[JobRecord, int]:
        '''Copy a finished job's original input and queue a retry.'''
        profiles = original.required_profiles()
        if isinstance(original, JobRecord):
            source_spec = original.spec
            source_file = source_spec.file
            source_path = original.paths.input_path
            jobs_root = original.paths.root
        else:
            source_spec = None
            source_file = UploadedFile(
                original.original_name, original.stored_name, original.size_bytes
            )
            source_path = original.input_path
            jobs_root = original.storage_root
        spec = JobSpec(
            job_id=new_job_id(set()),
            created_at=datetime.now(),
            config_file=source_spec.config_file if source_spec else original.config_file,
            file=source_file,
            submitted_by=owner,
            attempt_unlock=(
                source_spec.attempt_unlock if source_spec else original.attempt_unlock
            ),
            attempt_fix=source_spec.attempt_fix if source_spec else original.attempt_fix,
            skip_font_fix=skip_font_fix,
            attempt_targeted_fixes=(
                source_spec.attempt_targeted_fixes
                if source_spec else original.attempt_targeted_fixes
            ),
            require_wcag="wcag" in profiles,
            require_pdfua1="ua1" in profiles,
            verbose=source_spec.verbose if source_spec else original.verbose,
            parent_job_id=(source_spec.job_id if source_spec else original.job_id),
            attempt_number=(source_spec.attempt_number if source_spec
                            else original.attempt_number) + 1,
        )
        job = JobRecord(
            spec, JobState(), JobPaths(spec.job_id, source_file.stored_name, jobs_root)
        )
        try:
            job.paths.input_path.parent.mkdir(parents=True, exist_ok=True)
            job.paths.web_path.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, source_path, job.paths.input_path)
            save_meta(job)
            self._store.add_batch((job,), notify=False)
            positions = self._runner.submit(job.spec.job_id, owner)
            self._store.publish_job_added((job.spec.job_id,))
            return job, positions
        except Exception:
            self._runner.cancel(job.spec.job_id)
            self._store.remove(job.spec.job_id, notify=False)
            await self.discard_prepared([job])
            await asyncio.to_thread(delete_persisted_job, job.spec.job_id, jobs_root)
            raise

    async def delete_terminal(self, job: JobRecord | JobAccessSnapshot) -> None:
        '''Remove one terminal job while excluding concurrent artifact access.'''
        job_id = job.spec.job_id if isinstance(job, JobRecord) else job.job_id
        base_path = job.paths.base_path if isinstance(job, JobRecord) else job.base_path
        with self._store.job_artifact_lock(job_id):
            self._store.remove(job_id)
            await asyncio.to_thread(shutil.rmtree, base_path, True)
            jobs_root = job.paths.root if isinstance(job, JobRecord) else job.storage_root
            await asyncio.to_thread(delete_persisted_job, job_id, jobs_root)

    async def discard_prepared(self, jobs: list[JobRecord]) -> None:
        '''Remove uncommitted job directories after a workflow failure.'''
        if jobs:
            await asyncio.gather(*(
                asyncio.to_thread(shutil.rmtree, job.paths.base_path, True)
                for job in jobs
            ))
