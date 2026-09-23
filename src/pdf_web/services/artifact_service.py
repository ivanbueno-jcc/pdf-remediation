'''Prepare downloadable job artifacts outside route handlers.'''

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException

from ..bundle import build_bundle
from ..infrastructure.persistence import refresh_artifacts
from ..models import JobAccessSnapshot, JobRecord
from ..store import JobStore
from .artifact_paths import ArtifactPathService


class JobArtifactService:  # pylint: disable=too-few-public-methods
    '''Create or locate a job artifact while coordinating access and cleanup.'''

    def __init__(self, store: JobStore, paths: ArtifactPathService) -> None:
        self._store = store
        self._paths = paths

    async def bundle(self, job: JobAccessSnapshot | JobRecord) -> Path:
        '''Build the cached ZIP under the same lock used by deletion.'''
        job_id = job.spec.job_id if isinstance(job, JobRecord) else job.job_id
        with self._store.job_artifact_lock(job_id) as exists:
            if not exists:
                raise HTTPException(status_code=404, detail="Not found.")
            bundle_path = (
                job.paths.bundle_path if isinstance(job, JobRecord) else job.bundle_path
            )
            snapshot = self._store.snapshot(job_id)
            if snapshot is None:
                raise HTTPException(status_code=404, detail="Not found.")
            if not bundle_path.is_file():
                await asyncio.to_thread(build_bundle, snapshot, bundle_path)
            await asyncio.to_thread(refresh_artifacts, snapshot)
        return self._paths.require_file(bundle_path)
