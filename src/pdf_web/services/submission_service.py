'''Validate and prepare batches of uploaded jobs before atomic submission.'''

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import UploadFile

from ..config import JOBS_ROOT
from ..models import JobRecord
from ..runner import PipelineRunner
from ..store import JobStore
from .job_service import JobWorkflow
from ..submission import SubmissionOptions, prepare_uploaded_job


class SubmissionService:
    '''Prepare an upload batch, collecting per-file rejections.'''

    def __init__(self, store: JobStore, runner: PipelineRunner) -> None:
        self._workflow = JobWorkflow(store, runner)

    async def prepare_batch(  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
            self,
            uploads: list[UploadFile],
            options: SubmissionOptions,
            owner: str,
            new_job_id: Callable[[set[str]], str],
            jobs_root: Path = JOBS_ROOT,
    ) -> tuple[list[JobRecord], list[dict[str, str]]]:
        """Validate uploads and stage accepted jobs, cleaning up on failure."""
        created_at = datetime.now()
        ids: set[str] = set()
        names: set[str] = set()
        total_bytes = 0
        accepted: list[JobRecord] = []
        rejected: list[dict[str, str]] = []
        try:
            for upload in uploads:
                original_name = upload.filename or "upload.pdf"
                job, error, size = await prepare_uploaded_job(
                    upload, options, owner, created_at, ids, names, total_bytes,
                    new_job_id, jobs_root,
                )
                if error is not None or job is None:
                    rejected.append({
                        "original_name": original_name,
                        "reason": error or "Upload rejected.",
                    })
                    continue
                total_bytes += size
                accepted.append(job)
        except Exception:
            await self._workflow.discard_prepared(accepted)
            raise
        return accepted, rejected

    async def commit(self, jobs: list[JobRecord], owner: str) -> list[int]:
        '''Persist and enqueue the prepared batch as one visible operation.'''
        return await self._workflow.commit_submission(jobs, owner)
