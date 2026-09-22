'''Explicit HTTP representations for job and queue responses.'''

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class JobFileResponse(BaseModel):
    """Public metadata for an uploaded source file."""
    original_name: str
    stored_name: str
    size_bytes: int
    page_count: int | None = None


class JobResponse(BaseModel):
    """Complete public representation of one job."""
    job_id: str
    submitted_by: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str
    config_file: str
    attempt_unlock: bool
    attempt_fix: bool
    skip_font_fix: bool
    attempt_font_fix: bool
    attempt_targeted_fixes: bool
    wcag_and_ua1_must_pass: bool
    require_wcag: bool
    require_pdfua1: bool
    validation_requirement: str
    verbose: bool
    file: JobFileResponse
    stages: list[dict[str, Any]]
    outcome: str | None = None
    outcome_label: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    initially_secured: bool = False
    has_pdf: bool = False
    warnings: list[str]
    diagnostics: list[dict[str, str]]
    error: str | None = None
    jobs_ahead: int | None = None


class QueueJobResponse(BaseModel):
    """Compact job projection used by paginated queue views."""
    job_id: str
    name: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    page_count: int | None = None
    config_file: str
    config_label: str
    status: str
    outcome: str | None = None
    outcome_label: str | None = None
    stages_done: int
    current_stage: str | None = None
    jobs_ahead: int | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    initially_secured: bool
    validation_requirement: str
    has_pdf: bool
    error: str | None = None


class QueuePageResponse(BaseModel):
    """Queue page and owner-specific scheduling metadata."""
    concurrency: int
    your_limit: int
    your_running: int
    all_terminal: bool
    total_jobs: int
    processed_jobs: int
    queue_generation: int
    next_cursor: str | None = None
    jobs: list[QueueJobResponse]


class RejectedUploadResponse(BaseModel):
    """Reason an individual submitted file was rejected."""
    original_name: str
    reason: str


class SubmissionResponse(BaseModel):
    """Outcome of a batch submission request."""
    jobs: list[JobResponse]
    rejected: list[RejectedUploadResponse]
    concurrency: int
    your_limit: int
