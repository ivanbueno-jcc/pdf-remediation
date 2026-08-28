'''
Job and result data structures for the PDF remediation web application.
'''

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config import JOBS_ROOT, PROJECT_NAME, WEB_FOLDER_NAME, WORKSPACE_NAME

PIPELINE_STEPS: tuple[tuple[int, str], ...] = (
    (1, "validate (pre-fix)"),
    (2, "fix"),
    (3, "font_fix"),
    (4, "font_fix_pdfix"),
    (5, "reprocess"),
    (6, "fix_target"),
    (7, "validate (final)"),
)


class JobStatus(StrEnum):
    '''
    Lifecycle state of a remediation job.
    '''

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepState(StrEnum):
    '''
    Lifecycle state of a single pipeline step.
    '''

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
})


@dataclass
class UploadedFile:
    '''
    One PDF accepted from the browser.
    '''

    file_id: str
    original_name: str
    stored_name: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        '''
        Return a JSON-serializable view.
        '''
        return {
            "file_id": self.file_id,
            "original_name": self.original_name,
            "stored_name": self.stored_name,
            "size_bytes": self.size_bytes,
        }


@dataclass
class FileResult:
    '''
    Harvested outcome for one uploaded PDF.
    '''

    file_id: str
    outcome: str
    final_pdf_path: Path | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        '''
        Return a JSON-serializable view with compact profile summaries.
        '''
        return {
            "file_id": self.file_id,
            "outcome": self.outcome,
            "outcome_label": outcome_label(self.outcome),
            "has_pdf": self.final_pdf_path is not None,
            "before": summarize_report(self.before),
            "after": summarize_report(self.after),
            "note": self.note,
        }


OUTCOME_LABELS = {
    "remediated": "Remediated",
    "font-issues": "Font issues remain",
    "font-issues-missing-unicode": "Font issues (missing Unicode)",
    "unable-to-validate": "Unable to validate",
    "secured-needs-approval": "Secured, needs approval",
    "secured-cannot-process": "Secured, cannot process",
    "pdfix-unable-to-open": "PDFix could not open",
    "pdfix-cannot-process": "PDFix could not process",
    "unable-to-process": "Unable to process",
    "pending": "Working\u2026",
    "active": "Still not compliant",
    "unprocessed": "Not processed",
    "missing": "Not produced",
}


def outcome_label(outcome: str) -> str:
    '''
    Return a readable label for a routing outcome.
    '''
    return OUTCOME_LABELS.get(outcome, outcome)


def summarize_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    '''
    Reduce a validation report to the fields the browser renders.
    '''
    if report is None:
        return None
    profiles = report.get("profiles", {})
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
            for name, profile in profiles.items()
        },
    }


@dataclass
class Job:  # pylint: disable=too-many-instance-attributes
    '''
    One remediation run over a batch of uploaded PDFs.
    '''

    job_id: str
    created_at: datetime
    config_file: str
    submitted_by: str = ""
    skip_font_fix: bool = False
    wcag_and_ua1_must_pass: bool = False
    verbose: bool = False
    status: JobStatus = JobStatus.QUEUED
    files: list[UploadedFile] = field(default_factory=list)
    steps: dict[int, StepState] = field(
        default_factory=lambda: {
            number: StepState.PENDING for number, _ in PIPELINE_STEPS
        }
    )
    current_step: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    return_code: int | None = None
    error: str | None = None
    partial: bool = False
    results: list[FileResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def base_path(self) -> Path:
        '''
        Return the PROJECT_BASE_PATH handed to the pipeline subprocess.
        '''
        return JOBS_ROOT / self.job_id

    @property
    def project_path(self) -> Path:
        '''
        Return the ephemeral project directory.
        '''
        return self.base_path / PROJECT_NAME

    @property
    def source_path(self) -> Path:
        '''
        Return the seeded source folder.
        '''
        return self.project_path / "source"

    @property
    def workspace_path(self) -> Path:
        '''
        Return the workspace the pipeline writes into.
        '''
        return self.project_path / "workspace" / WORKSPACE_NAME

    @property
    def web_path(self) -> Path:
        '''
        Return the folder holding web-app-owned artifacts.
        '''
        return self.base_path / WEB_FOLDER_NAME

    @property
    def log_path(self) -> Path:
        '''
        Return the captured pipeline log path.
        '''
        return self.web_path / "pipeline.log"

    @property
    def meta_path(self) -> Path:
        '''
        Return the persisted job metadata path.
        '''
        return self.web_path / "meta.json"

    @property
    def bundle_path(self) -> Path:
        '''
        Return the cached ZIP bundle path.
        '''
        return self.web_path / "bundle.zip"

    def result_folder(self, file_id: str) -> Path:
        '''
        Return the folder holding one file's normalized reports.
        '''
        return self.web_path / "results" / file_id

    def find_file(self, file_id: str) -> UploadedFile | None:
        '''
        Return the upload with the given identifier.
        '''
        for uploaded_file in self.files:
            if uploaded_file.file_id == file_id:
                return uploaded_file
        return None

    def find_result(self, file_id: str) -> FileResult | None:
        '''
        Return the harvested result with the given identifier.
        '''
        for result in self.results:
            if result.file_id == file_id:
                return result
        return None

    def is_terminal(self) -> bool:
        '''
        Return whether the job has finished running.
        '''
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        '''
        Return a JSON-serializable view for the browser.
        '''
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
            "skip_font_fix": self.skip_font_fix,
            "wcag_and_ua1_must_pass": self.wcag_and_ua1_must_pass,
            "verbose": self.verbose,
            "current_step": self.current_step,
            "steps": [
                {
                    "number": number,
                    "name": name,
                    "state": str(self.steps.get(number, StepState.PENDING)),
                }
                for number, name in PIPELINE_STEPS
            ],
            "files": [uploaded_file.to_dict() for uploaded_file in self.files],
            "results": [result.to_dict() for result in self.results],
            "summary": self.summary,
            "return_code": self.return_code,
            "error": self.error,
            "partial": self.partial,
        }
