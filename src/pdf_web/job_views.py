'''Consistent, owner-scoped projections of mutable jobs for HTTP responses.'''

from __future__ import annotations

from typing import Any

from .config import CONFIG_FILE_DETAILS
from .models import Job, JobStatus, outcome_label, summarize_report


def queue_payload(job: Job, pending_positions: dict[str, int]) -> dict[str, Any]:
    '''Build one queue row from one locked job state.'''
    with job.state_lock:
        return {
            "job_id": job.job_id,
            "name": job.file.original_name,
            "created_at": job.created_at.isoformat(timespec="seconds"),
            "started_at": job.started_at.isoformat(timespec="seconds")
            if job.started_at else None,
            "finished_at": job.finished_at.isoformat(timespec="seconds")
            if job.finished_at else None,
            "page_count": job.file.page_count,
            "config_file": job.config_file,
            "config_label": CONFIG_FILE_DETAILS.get(job.config_file, {}).get(
                "label", job.config_file
            ),
            "status": str(job.status),
            "outcome": job.outcome,
            "outcome_label": outcome_label(job.outcome),
            "stages_done": len(job.stages),
            "current_stage": job.stages[-1]["name"] if job.stages else None,
            "jobs_ahead": pending_positions.get(
                job.job_id, 0 if job.status == JobStatus.RUNNING else None
            ),
            "before": summarize_report(job.result.before if job.result else None),
            "after": summarize_report(job.result.after if job.result else None),
            "initially_secured": job.initially_secured,
            "validation_requirement": job.validation_requirement,
            "has_pdf": job.artifact("pdf") is not None,
            "error": job.error,
        }
