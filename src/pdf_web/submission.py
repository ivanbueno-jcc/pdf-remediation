'''Validation and preparation of browser PDF submissions.'''

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from fastapi import HTTPException, UploadFile

from .config import (
    ALLOWED_CONFIG_FILES,
    CONFIG_DIR,
    MAX_SUBMISSION_BYTES,
)
from .models import Job, JobProcessingOptions, UploadedFile
from .uploads import (
    UploadError,
    looks_like_pdf,
    sanitize_upload_name,
    write_upload_stream,
)


@dataclass(frozen=True)
class SubmissionOptions(JobProcessingOptions):
    '''Validated options shared by every file in one submission.'''


def validate_options(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        config_file: str,
        attempt_unlock: bool,
        attempt_fix: bool,
        attempt_font_fix: bool | None,
        skip_font_fix: bool,
        attempt_targeted_fixes: bool,
        require_wcag: bool,
        require_pdfua1: bool,
        wcag_and_ua1_must_pass: bool | None,
        verbose: bool) -> SubmissionOptions:
    '''Validate form options once before processing any uploaded file.'''
    if config_file not in ALLOWED_CONFIG_FILES:
        raise HTTPException(
            status_code=400, detail=f"Unknown configuration file: {config_file}"
        )
    if not (CONFIG_DIR / config_file).is_file():
        raise HTTPException(
            status_code=400, detail=f"Configuration file is missing: {config_file}"
        )

    should_attempt_font_fix = (
        attempt_font_fix if attempt_font_fix is not None else not skip_font_fix
    )
    if wcag_and_ua1_must_pass:
        require_wcag = True
        require_pdfua1 = True
    if not require_wcag and not require_pdfua1:
        raise HTTPException(
            status_code=400, detail="Select WCAG, PDF/UA-1, or both."
        )

    return SubmissionOptions(
        config_file=config_file,
        attempt_unlock=attempt_unlock,
        attempt_fix=attempt_fix,
        skip_font_fix=not should_attempt_font_fix,
        attempt_targeted_fixes=attempt_targeted_fixes,
        require_wcag=require_wcag,
        require_pdfua1=require_pdfua1,
        verbose=verbose,
    )


def _iterate_upload(upload: UploadFile):
    '''Yield an upload's contents in bounded chunks.'''
    upload.file.seek(0)
    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            return
        yield chunk


async def prepare_uploaded_job(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        upload: UploadFile,
        options: SubmissionOptions,
        user: str,
        created_at: datetime,
        taken_ids: set[str],
        taken_names: set[str],
        previous_bytes: int,
        new_job_id: Callable[[set[str]], str]) -> tuple[Job | None, str | None, int]:
    '''Write and validate one upload, returning a rejection reason if needed.'''
    original_name = upload.filename or "upload.pdf"
    job: Job | None = None
    try:
        stored_name = sanitize_upload_name(original_name, taken_names)
        job = Job(
            job_id=new_job_id(taken_ids),
            created_at=created_at,
            config_file=options.config_file,
            file=UploadedFile(original_name, stored_name, 0),
            submitted_by=user,
            attempt_unlock=options.attempt_unlock,
            attempt_fix=options.attempt_fix,
            skip_font_fix=options.skip_font_fix,
            attempt_targeted_fixes=options.attempt_targeted_fixes,
            require_wcag=options.require_wcag,
            require_pdfua1=options.require_pdfua1,
            verbose=options.verbose,
        )
        job.input_path.parent.mkdir(parents=True, exist_ok=True)
        job.web_path.mkdir(parents=True, exist_ok=True)

        size = await asyncio.to_thread(
            write_upload_stream, _iterate_upload(upload),
            job.input_path, original_name
        )
        if not looks_like_pdf(job.input_path):
            raise UploadError(f"File is not a PDF: {original_name}")

        if previous_bytes + size > MAX_SUBMISSION_BYTES:
            raise UploadError(
                f"Submission exceeds the {MAX_SUBMISSION_BYTES} byte limit."
            )
        with job.state_lock:
            job.file = replace(job.file, size_bytes=size)
        return job, None, size
    except UploadError as error:
        if job is not None:
            shutil.rmtree(job.base_path, ignore_errors=True)
        return None, str(error), 0
    except Exception:
        if job is not None:
            shutil.rmtree(job.base_path, ignore_errors=True)
        raise
