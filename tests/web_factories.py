'''Shared builders for web application test fixtures.'''

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pdf_web.models import FileResult, Job, JobStatus, UploadedFile

DEFAULT_CREATED_AT = datetime(2026, 8, 27, 12, 0, 0)


def make_job(
        job_id: str = "20260827-120000-aaaaaa",
        submitted_by: str = "",
        status: JobStatus = JobStatus.QUEUED,
        config_file: str = "default-slim.json") -> Job:
    '''
    Build a job with one uploaded file and no on-disk artifacts.
    '''
    job = Job(
        job_id=job_id,
        created_at=DEFAULT_CREATED_AT,
        config_file=config_file,
        submitted_by=submitted_by,
        skip_font_fix=True,
        status=status,
    )
    job.files.append(UploadedFile("000", "Report v2.pdf", "Report_v2.pdf", 1234))
    return job


def write_job_artifacts(job: Job) -> Path:
    '''
    Create the log, remediated PDF, and stage reports a finished job leaves behind.

    Returns the remediated PDF path.
    '''
    job.web_path.mkdir(parents=True, exist_ok=True)
    job.log_path.write_text("pipeline output\n", encoding="utf-8")

    pdf_path = job.workspace_path / "remediated" / "files" / job.files[0].stored_name
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.7\n")

    results = job.result_folder("000")
    results.mkdir(parents=True, exist_ok=True)
    (results / "before.json").write_text('{"status": "fail"}', encoding="utf-8")
    (results / "after.json").write_text('{"status": "pass"}', encoding="utf-8")
    return pdf_path


def add_completed_result(job: Job, pdf_path: Path) -> None:
    '''
    Attach a harvested result describing a successfully remediated file.
    '''
    job.results.append(FileResult(
        file_id="000",
        outcome="remediated",
        final_pdf_path=pdf_path,
        before={"status": "fail", "profiles": {}},
        after={"status": "pass", "profiles": {}},
    ))
