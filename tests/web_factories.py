'''Shared builders for web application test fixtures.'''

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pdf_api.models import PipelineResult, PipelineStatus

from pdf_web.models import Job, JobStatus, UploadedFile

DEFAULT_CREATED_AT = datetime(2026, 8, 27, 12, 0, 0)


def make_job(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        job_id: str = "20260827-120000-aaaaaa",
        submitted_by: str = "",
        status: JobStatus = JobStatus.QUEUED,
        config_file: str = "default-slim.json",
        original_name: str = "Report v2.pdf",
        stored_name: str = "Report_v2.pdf") -> Job:
    '''
    Build a job for one PDF, with no on-disk artifacts.
    '''
    return Job(
        job_id=job_id,
        created_at=DEFAULT_CREATED_AT,
        config_file=config_file,
        file=UploadedFile(original_name, stored_name, 1234),
        submitted_by=submitted_by,
        skip_font_fix=True,
        status=status,
    )


def write_job_artifacts(job: Job) -> Path:
    '''
    Create the log, remediated PDF, and reports a finished job leaves behind.
    '''
    job.web_path.mkdir(parents=True, exist_ok=True)
    job.log_path.write_text("pipeline output\n", encoding="utf-8")

    job.input_path.parent.mkdir(parents=True, exist_ok=True)
    job.input_path.write_bytes(b"%PDF-1.7\n")

    job.output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = job.output_dir / job.file.stored_name
    pdf_path.write_bytes(b"%PDF-1.7\n")
    (job.output_dir / "before.json").write_text('{"status": "fail"}', encoding="utf-8")
    (job.output_dir / "after.json").write_text('{"status": "pass"}', encoding="utf-8")
    return pdf_path


def add_completed_result(job: Job, pdf_path: Path) -> None:
    '''
    Attach a pipeline result describing a successfully remediated file.
    '''
    job.outcome = str(PipelineStatus.REMEDIATED)
    job.result = PipelineResult(
        status=PipelineStatus.REMEDIATED,
        input_pdf_path=job.input_path,
        output_pdf_path=pdf_path,
        before={"status": "fail", "passed": False, "failed_rules_count": 1, "profiles": {}},
        after={"status": "pass", "passed": True, "failed_rules_count": 0, "profiles": {}},
    )
