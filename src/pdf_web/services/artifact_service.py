'''Prepare downloadable job artifacts outside route handlers.'''

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException

from ..bundle import build_bundle
from ..models import JobAccessSnapshot
from ..store import JobStore


class JobArtifactService:
    '''Create or locate a job artifact while coordinating access and cleanup.'''

    def __init__(self, store: JobStore, jobs_root: Path) -> None:
        self._store = store
        self._jobs_root = jobs_root

    def require_file(self, candidate: Path) -> Path:
        '''Resolve a file only when it remains under the configured job root.'''
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._jobs_root.resolve()) or not resolved.is_file():
            raise HTTPException(status_code=404, detail="Not found.")
        return resolved

    async def bundle(self, job: JobAccessSnapshot) -> Path:
        '''Build the cached ZIP under the same lock used by deletion.'''
        with self._store.job_artifact_lock(job.job_id) as exists:
            if not exists:
                raise HTTPException(status_code=404, detail="Not found.")
            if not job.bundle_path.is_file():
                snapshot = self._store.snapshot(job.job_id)
                if snapshot is None:
                    raise HTTPException(status_code=404, detail="Not found.")
                await asyncio.to_thread(build_bundle, snapshot, job.bundle_path)
        return self.require_file(job.bundle_path)
