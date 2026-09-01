'''
Job and result structures for the PDF remediation web application.

One job is one PDF. Progress is the pipeline's own stage list rather than a
fixed set of steps scraped from console output, so a job reports what actually
happened to it: which stages ran, which were skipped, and why.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pdf_api.models import PipelineResult, PipelineStatus
from pdf_api.pipeline import artifact_path

from .config import JOBS_ROOT, WEB_FOLDER_NAME


class JobStatus(StrEnum):
    '''
    Lifecycle state of a remediation job.
    '''

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
})

# How a finished run reads to somebody who never sees a workspace.
OUTCOME_LABELS = {
    str(PipelineStatus.ALREADY_COMPLIANT): "Already compliant",
    str(PipelineStatus.REMEDIATED): "Remediated",
    str(PipelineStatus.IMPROVED): "Improved, still failing",
    str(PipelineStatus.UNCHANGED): "Unchanged",
    str(PipelineStatus.FAILED): "Failed",
    str(PipelineStatus.CANCELLED): "Cancelled",
}


def outcome_label(outcome: str | None) -> str | None:
    '''
    Return a readable label for a pipeline outcome.
    '''
    if outcome is None:
        return None
    return OUTCOME_LABELS.get(outcome, outcome)


def status_for(outcome: PipelineStatus) -> JobStatus:
    '''
    Map a pipeline outcome onto the job lifecycle.

    Improved and unchanged are completed runs: remediation ran and reported
    honestly. Only an inability to run is a failure.
    '''
    if outcome == PipelineStatus.CANCELLED:
        return JobStatus.CANCELLED
    if outcome == PipelineStatus.FAILED:
        return JobStatus.FAILED
    return JobStatus.COMPLETED


@dataclass
class UploadedFile:
    '''
    The PDF a job was created for.
    '''

    original_name: str
    stored_name: str
    size_bytes: int
    page_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        '''
        Return a JSON-serializable view.
        '''
        return {
            "original_name": self.original_name,
            "stored_name": self.stored_name,
            "size_bytes": self.size_bytes,
            "page_count": self.page_count,
        }


def summarize_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    '''
    Reduce a validation report to what the job list renders.

    Violations are deliberately excluded: they are large, and the detail view
    fetches the full report on demand.
    '''
    if report is None:
        return None
    return {
        "status": report.get("status"),
        "passed": report.get("passed"),
        "failed_rules_count": report.get("failed_rules_count", 0),
        "profiles": {
            name: {
                "status": profile.get("status"),
                "passed": profile.get("passed"),
                "failed_rules_count": profile.get("failed_rules_count", 0),
            }
            for name, profile in report.get("profiles", {}).items()
        },
    }


@dataclass
class Job:  # pylint: disable=too-many-instance-attributes
    '''
    One PDF moving through the remediation pipeline.
    '''

    job_id: str
    created_at: datetime
    config_file: str
    file: UploadedFile
    submitted_by: str = ""
    attempt_unlock: bool = True
    attempt_fix: bool = True
    skip_font_fix: bool = False
    attempt_targeted_fixes: bool = True
    wcag_and_ua1_must_pass: bool = False
    verbose: bool = False
    status: JobStatus = JobStatus.QUEUED
    stages: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: PipelineResult | None = None
    outcome: str | None = None
    error: str | None = None

    @property
    def base_path(self) -> Path:
        '''
        Return the directory holding everything for this job.
        '''
        return JOBS_ROOT / self.job_id

    @property
    def input_path(self) -> Path:
        '''
        Return the uploaded PDF.
        '''
        return self.base_path / "input" / self.file.stored_name

    @property
    def output_dir(self) -> Path:
        '''
        Return the directory the pipeline writes its artifacts into.
        '''
        return self.base_path / "output"

    @property
    def web_path(self) -> Path:
        '''
        Return the folder holding web-app-owned artifacts.
        '''
        return self.base_path / WEB_FOLDER_NAME

    @property
    def log_path(self) -> Path:
        '''
        Return the captured run log.
        '''
        return self.web_path / "pipeline.log"

    @property
    def meta_path(self) -> Path:
        '''
        Return the persisted job metadata.
        '''
        return self.web_path / "meta.json"

    @property
    def bundle_path(self) -> Path:
        '''
        Return the cached ZIP bundle.
        '''
        return self.web_path / "bundle.zip"

    def artifact(self, name: str) -> Path | None:
        '''
        Return one downloadable artifact, if the pipeline produced it.
        '''
        return artifact_path(
            self.output_dir,
            name,
            self.result.output_pdf_path if self.result else None,
        )

    def is_terminal(self) -> bool:
        '''
        Return whether the job has finished.
        '''
        return self.status in TERMINAL_STATUSES

    @property
    def initially_secured(self) -> bool:
        '''Return whether the uploaded PDF was secured before processing.'''
        if self.result is not None and self.result.initially_secured is not None:
            return self.result.initially_secured
        return any(
            stage.get("name") == "unlock" and stage.get("status") == "ok"
            for stage in self.stages
        )

    def to_dict(self) -> dict[str, Any]:
        '''
        Return a JSON-serializable view for the browser.
        '''
        result = self.result
        return {
            "job_id": self.job_id,
            "submitted_by": self.submitted_by,
            "created_at": self.created_at.isoformat(timespec="seconds"),
            "started_at": (
                self.started_at.isoformat(timespec="seconds")
                if self.started_at else None
            ),
            "finished_at": (
                self.finished_at.isoformat(timespec="seconds")
                if self.finished_at else None
            ),
            "status": str(self.status),
            "config_file": self.config_file,
            "attempt_unlock": self.attempt_unlock,
            "attempt_fix": self.attempt_fix,
            "skip_font_fix": self.skip_font_fix,
            "attempt_font_fix": not self.skip_font_fix,
            "attempt_targeted_fixes": self.attempt_targeted_fixes,
            "wcag_and_ua1_must_pass": self.wcag_and_ua1_must_pass,
            "verbose": self.verbose,
            "file": self.file.to_dict(),
            "stages": self.stages,
            "outcome": self.outcome,
            "outcome_label": outcome_label(self.outcome),
            "before": summarize_report(result.before if result else None),
            "after": summarize_report(result.after if result else None),
            "initially_secured": self.initially_secured,
            "has_pdf": self.artifact("pdf") is not None,
            "warnings": list(result.warnings) if result else [],
            "diagnostics": list(result.diagnostics) if result else [],
            "error": self.error,
        }
