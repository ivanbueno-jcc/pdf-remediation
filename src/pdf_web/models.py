'''
Job and result structures for the PDF remediation web application.

One job is one PDF. Progress is the pipeline's own stage list rather than a
fixed set of steps scraped from console output, so a job reports what actually
happened to it: which stages ran, which were skipped, and why.
'''

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
import threading
from typing import Any

from pdf_api.models import (
    PipelineResult,
    PipelineStatus,
    selected_validation_profiles,
)
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


@dataclass(frozen=True)
class QueueJobSnapshot:  # pylint: disable=too-many-instance-attributes
    '''Compact immutable projection used by queue and list responses.'''

    job_id: str
    name: str
    page_count: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    config_file: str
    status: JobStatus
    outcome: str | None
    stages_done: int
    current_stage: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    initially_secured: bool
    validation_requirement: str
    has_pdf: bool
    error: str | None


@dataclass(frozen=True)
class JobProcessingOptions:  # pylint: disable=too-many-instance-attributes
    '''Immutable pipeline options shared by jobs and submission validation.'''

    config_file: str
    attempt_unlock: bool
    attempt_fix: bool
    skip_font_fix: bool
    attempt_targeted_fixes: bool
    require_wcag: bool
    require_pdfua1: bool
    verbose: bool


@dataclass(frozen=True)
class JobAccessSnapshot(JobProcessingOptions):  # pylint: disable=too-many-instance-attributes
    '''Small immutable view used to authorize and serve job file operations.'''

    job_id: str
    submitted_by: str
    status: JobStatus
    original_name: str
    stored_name: str
    size_bytes: int
    output_pdf_path: Path | None

    @property
    def base_path(self) -> Path:
        '''Return the directory holding this job's files.'''
        return JOBS_ROOT / self.job_id

    @property
    def input_path(self) -> Path:
        '''Return the uploaded PDF path.'''
        return self.base_path / "input" / self.stored_name

    @property
    def output_dir(self) -> Path:
        '''Return the pipeline output directory.'''
        return self.base_path / "output"

    @property
    def web_path(self) -> Path:
        '''Return the web-artifact directory.'''
        return self.base_path / WEB_FOLDER_NAME

    @property
    def log_path(self) -> Path:
        '''Return the captured pipeline log path.'''
        return self.web_path / "pipeline.log"

    @property
    def bundle_path(self) -> Path:
        '''Return the cached bundle path.'''
        return self.web_path / "bundle.zip"

    def artifact(self, name: str) -> Path | None:
        '''Return an existing output artifact, if available.'''
        return artifact_path(self.output_dir, name, self.output_pdf_path)

    def is_terminal(self) -> bool:
        '''Return whether the job has finished.'''
        return self.status in TERMINAL_STATUSES

    def required_profiles(self) -> tuple[str, ...]:
        '''Return the validation profiles selected for this job.'''
        return selected_validation_profiles(self.require_wcag, self.require_pdfua1, False)


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


@dataclass(frozen=True)
class JobSpec:  # pylint: disable=too-many-instance-attributes
    '''Immutable identity, upload, and processing choices fixed at submission.'''

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
    require_wcag: bool = True
    require_pdfua1: bool = False
    verbose: bool = False


@dataclass
class JobState:  # pylint: disable=too-many-instance-attributes
    '''Mutable lifecycle and pipeline output, protected by its state lock.'''

    status: JobStatus = JobStatus.QUEUED
    stages: list[dict[str, Any]] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: PipelineResult | None = None
    outcome: str | None = None
    error: str | None = None
    page_count: int | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    bundle_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


@dataclass(frozen=True)
class JobPaths:
    '''Filesystem layout derived from job identity and the configured root.'''

    job_id: str
    stored_name: str
    root: Path = JOBS_ROOT

    @property
    def base_path(self) -> Path:
        """Return this job's storage directory."""
        return self.root / self.job_id

    @property
    def input_path(self) -> Path:
        """Return the uploaded source PDF path."""
        return self.base_path / "input" / self.stored_name

    @property
    def output_dir(self) -> Path:
        """Return the pipeline output directory."""
        return self.base_path / "output"

    @property
    def web_path(self) -> Path:
        """Return the web-specific job data directory."""
        return self.base_path / WEB_FOLDER_NAME

    @property
    def log_path(self) -> Path:
        """Return the captured pipeline log path."""
        return self.web_path / "pipeline.log"

    @property
    def meta_path(self) -> Path:
        """Return the persisted metadata path."""
        return self.web_path / "meta.json"

    @property
    def bundle_path(self) -> Path:
        """Return the generated artifact archive path."""
        return self.web_path / "bundle.zip"


class JobSerializer:  # pylint: disable=too-few-public-methods
    '''Explicit browser response projection, independent of persisted state.'''

    @staticmethod
    def from_record(job: "JobRecord") -> dict[str, Any]:
        """Project a job record into its public JSON representation."""
        with job.state.lock:
            spec = job.spec
            state = job.state
            result = state.result
            stages = copy.deepcopy(state.stages)
            profiles = job.required_profiles()
            return {
                "job_id": spec.job_id,
                "submitted_by": spec.submitted_by,
                "created_at": spec.created_at.isoformat(timespec="seconds"),
                "started_at": (
                    state.started_at.isoformat(timespec="seconds") if state.started_at else None
                ),
                "finished_at": (
                    state.finished_at.isoformat(timespec="seconds") if state.finished_at else None
                ),
                "status": str(state.status),
                "config_file": spec.config_file,
                "attempt_unlock": spec.attempt_unlock,
                "attempt_fix": spec.attempt_fix,
                "skip_font_fix": spec.skip_font_fix,
                "attempt_font_fix": not spec.skip_font_fix,
                "attempt_targeted_fixes": spec.attempt_targeted_fixes,
                "wcag_and_ua1_must_pass": len(profiles) == 2,
                "require_wcag": "wcag" in profiles,
                "require_pdfua1": "ua1" in profiles,
                "validation_requirement": job.validation_requirement,
                "verbose": spec.verbose,
                "file": replace(spec.file, page_count=state.page_count).to_dict(),
                "stages": stages,
                "outcome": state.outcome,
                "outcome_label": outcome_label(state.outcome),
                "before": summarize_report(result.before if result else None),
                "after": summarize_report(result.after if result else None),
                "initially_secured": job.initially_secured,
                "has_pdf": job.artifact("pdf") is not None,
                "warnings": list(result.warnings) if result else [],
                "diagnostics": list(result.diagnostics) if result else [],
                "error": state.error,
            }


class JobRecord:  # pylint: disable=too-many-public-methods
    '''Composition root for a job's immutable spec, mutable state, and paths.'''

    def __init__(self, spec: JobSpec, state: JobState | None = None,
                 paths: JobPaths | None = None) -> None:
        """Create a record from its explicitly owned value objects."""
        self.spec = replace(spec, file=replace(spec.file, page_count=None))
        self.state = state or JobState(page_count=spec.file.page_count)
        self.paths = paths or JobPaths(spec.job_id, spec.file.stored_name, JOBS_ROOT)

    def __copy__(self) -> "JobRecord":
        '''Create a detached state snapshot with independent synchronization.'''
        snapshot_spec = replace(self.spec, file=copy.deepcopy(self.spec.file))
        snapshot_state = JobState(
            status=self.state.status,
            stages=copy.deepcopy(self.state.stages),
            started_at=self.state.started_at,
            finished_at=self.state.finished_at,
            result=copy.deepcopy(self.state.result),
            outcome=self.state.outcome,
            error=self.state.error,
            page_count=self.state.page_count,
        )
        return JobRecord(snapshot_spec, snapshot_state, self.paths)

    # Temporary, explicit migration accessors. Production code uses spec/state/paths.
    @property
    def job_id(self) -> str:
        """Compatibility view of ``spec.job_id``."""
        return self.spec.job_id

    @property
    def created_at(self) -> datetime:
        """Compatibility view of ``spec.created_at``."""
        return self.spec.created_at

    @property
    def config_file(self) -> str:
        """Compatibility view of ``spec.config_file``."""
        return self.spec.config_file

    @property
    def file(self) -> UploadedFile:
        """Return file metadata with its mutable page-count projection."""
        return replace(self.spec.file, page_count=self.state.page_count)

    @file.setter
    def file(self, value: UploadedFile) -> None:
        self.spec = replace(self.spec, file=replace(value, page_count=None))
        self.state.page_count = value.page_count

    @property
    def submitted_by(self) -> str:
        """Compatibility view of ``spec.submitted_by``."""
        return self.spec.submitted_by

    @submitted_by.setter
    def submitted_by(self, value: str) -> None:
        self.spec = replace(self.spec, submitted_by=value)

    @property
    def status(self) -> JobStatus:
        """Compatibility view of ``state.status``."""
        return self.state.status

    @status.setter
    def status(self, value: JobStatus) -> None:
        self.state.status = value

    @property
    def stages(self) -> list[dict[str, Any]]:
        """Compatibility view of ``state.stages``."""
        return self.state.stages

    @stages.setter
    def stages(self, value: list[dict[str, Any]]) -> None:
        self.state.stages = value

    @property
    def started_at(self) -> datetime | None:
        """Compatibility view of ``state.started_at``."""
        return self.state.started_at

    @started_at.setter
    def started_at(self, value: datetime | None) -> None:
        self.state.started_at = value

    @property
    def finished_at(self) -> datetime | None:
        """Compatibility view of ``state.finished_at``."""
        return self.state.finished_at

    @finished_at.setter
    def finished_at(self, value: datetime | None) -> None:
        self.state.finished_at = value

    @property
    def result(self) -> PipelineResult | None:
        """Compatibility view of ``state.result``."""
        return self.state.result

    @result.setter
    def result(self, value: PipelineResult | None) -> None:
        self.state.result = value

    @property
    def outcome(self) -> str | None:
        """Compatibility view of ``state.outcome``."""
        return self.state.outcome

    @outcome.setter
    def outcome(self, value: str | None) -> None:
        self.state.outcome = value

    @property
    def error(self) -> str | None:
        """Compatibility view of ``state.error``."""
        return self.state.error

    @error.setter
    def error(self, value: str | None) -> None:
        self.state.error = value

    @property
    def page_count(self) -> int | None:
        """Compatibility view of ``state.page_count``."""
        return self.state.page_count

    @page_count.setter
    def page_count(self, value: int | None) -> None:
        self.state.page_count = value

    @property
    def attempt_unlock(self) -> bool:
        """Compatibility view of ``spec.attempt_unlock``."""
        return self.spec.attempt_unlock

    @property
    def attempt_fix(self) -> bool:
        """Compatibility view of ``spec.attempt_fix``."""
        return self.spec.attempt_fix

    @property
    def skip_font_fix(self) -> bool:
        """Compatibility view of ``spec.skip_font_fix``."""
        return self.spec.skip_font_fix

    @property
    def attempt_targeted_fixes(self) -> bool:
        """Compatibility view of ``spec.attempt_targeted_fixes``."""
        return self.spec.attempt_targeted_fixes

    @property
    def wcag_and_ua1_must_pass(self) -> bool:
        """Compatibility view of ``spec.wcag_and_ua1_must_pass``."""
        return self.spec.wcag_and_ua1_must_pass

    @property
    def require_wcag(self) -> bool:
        """Compatibility view of ``spec.require_wcag``."""
        return self.spec.require_wcag

    @require_wcag.setter
    def require_wcag(self, value: bool) -> None:
        self.spec = replace(self.spec, require_wcag=value)

    @property
    def require_pdfua1(self) -> bool:
        """Compatibility view of ``spec.require_pdfua1``."""
        return self.spec.require_pdfua1

    @require_pdfua1.setter
    def require_pdfua1(self, value: bool) -> None:
        self.spec = replace(self.spec, require_pdfua1=value)

    @property
    def verbose(self) -> bool:
        """Compatibility view of ``spec.verbose``."""
        return self.spec.verbose

    @property
    def base_path(self) -> Path:
        """Compatibility view of ``paths.base_path``."""
        return self.paths.base_path

    @property
    def input_path(self) -> Path:
        """Compatibility view of ``paths.input_path``."""
        return self.paths.input_path

    @property
    def output_dir(self) -> Path:
        """Compatibility view of ``paths.output_dir``."""
        return self.paths.output_dir

    @property
    def web_path(self) -> Path:
        """Compatibility view of ``paths.web_path``."""
        return self.paths.web_path

    @property
    def log_path(self) -> Path:
        """Compatibility view of ``paths.log_path``."""
        return self.paths.log_path

    @property
    def meta_path(self) -> Path:
        """Compatibility view of ``paths.meta_path``."""
        return self.paths.meta_path

    @property
    def bundle_path(self) -> Path:
        """Compatibility view of ``paths.bundle_path``."""
        return self.paths.bundle_path

    @property
    def initially_secured(self) -> bool:
        """Report whether the source PDF required and completed unlocking."""
        with self.state.lock:
            if self.state.result is not None and self.state.result.initially_secured is not None:
                return self.state.result.initially_secured
            return any(
                stage.get("name") == "unlock" and stage.get("status") == "ok"
                for stage in self.state.stages
            )

    @property
    def validation_requirement(self) -> str:
        """Describe which validation profiles were selected."""
        profiles = self.required_profiles()
        if profiles == ("wcag",):
            return "wcag only"
        if profiles == ("ua1",):
            return "pdfua1 only"
        if profiles == ("wcag", "ua1"):
            return "wcag and pdfua1"
        return "no validation profile"

    def artifact(self, name: str) -> Path | None:
        """Resolve an available artifact path by its public artifact name."""
        with self.state.lock:
            return artifact_path(
                self.paths.output_dir, name,
                self.state.result.output_pdf_path if self.state.result else None,
            )

    def is_terminal(self) -> bool:
        """Return whether processing has reached a terminal state."""
        with self.state.lock:
            return self.state.status in TERMINAL_STATUSES

    def required_profiles(self) -> tuple[str, ...]:
        """Return validation profiles required by this job's specification."""
        return selected_validation_profiles(
            self.spec.require_wcag, self.spec.require_pdfua1,
            self.spec.wcag_and_ua1_must_pass,
        )

    def to_dict(self) -> dict[str, Any]:
        '''Build the persisted response projection.'''
        return JobSerializer.from_record(self)
