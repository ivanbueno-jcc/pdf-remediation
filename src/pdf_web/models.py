'''
Job and result structures for the PDF remediation web application.

One job is one PDF. Progress is the pipeline's own stage list rather than a
fixed set of steps scraped from console output, so a job reports what actually
happened to it: which stages ran, which were skipped, and why.
'''

from __future__ import annotations

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


@dataclass(frozen=True)
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
class JobSpec:
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
class JobState:
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
        return self.root / self.job_id

    @property
    def input_path(self) -> Path:
        return self.base_path / "input" / self.stored_name

    @property
    def output_dir(self) -> Path:
        return self.base_path / "output"

    @property
    def web_path(self) -> Path:
        return self.base_path / WEB_FOLDER_NAME

    @property
    def log_path(self) -> Path:
        return self.web_path / "pipeline.log"

    @property
    def meta_path(self) -> Path:
        return self.web_path / "meta.json"

    @property
    def bundle_path(self) -> Path:
        return self.web_path / "bundle.zip"


class JobSerializer:
    '''Explicit browser response projection, independent of persisted state.'''

    @staticmethod
    def from_record(job: "JobRecord") -> dict[str, Any]:
        with job.state_lock:
            result = job.result
            profiles = job.required_profiles()
            return {
                "job_id": job.job_id,
                "submitted_by": job.submitted_by,
                "created_at": job.created_at.isoformat(timespec="seconds"),
                "started_at": job.started_at.isoformat(timespec="seconds") if job.started_at else None,
                "finished_at": job.finished_at.isoformat(timespec="seconds") if job.finished_at else None,
                "status": str(job.status),
                "config_file": job.config_file,
                "attempt_unlock": job.attempt_unlock,
                "attempt_fix": job.attempt_fix,
                "skip_font_fix": job.skip_font_fix,
                "attempt_font_fix": not job.skip_font_fix,
                "attempt_targeted_fixes": job.attempt_targeted_fixes,
                "wcag_and_ua1_must_pass": len(profiles) == 2,
                "require_wcag": "wcag" in profiles,
                "require_pdfua1": "ua1" in profiles,
                "validation_requirement": job.validation_requirement,
                "verbose": job.verbose,
                "file": job.file.to_dict(),
                "stages": job.stages,
                "outcome": job.outcome,
                "outcome_label": outcome_label(job.outcome),
                "before": summarize_report(result.before if result else None),
                "after": summarize_report(result.after if result else None),
                "initially_secured": job.initially_secured,
                "has_pdf": job.artifact("pdf") is not None,
                "warnings": list(result.warnings) if result else [],
                "diagnostics": list(result.diagnostics) if result else [],
                "error": job.error,
            }


class JobRecord:
    '''Composition root for a job's immutable spec, mutable state, and paths.'''

    _SPEC_FIELDS = frozenset(JobSpec.__dataclass_fields__)
    _STATE_FIELDS = frozenset(JobState.__dataclass_fields__)

    def __init__(self, job_id: str, created_at: datetime, config_file: str,
                 file: UploadedFile, **values: Any) -> None:
        spec_values = {name: values.pop(name) for name in tuple(values) if name in self._SPEC_FIELDS}
        state_values = {name: values.pop(name) for name in tuple(values) if name in self._STATE_FIELDS}
        # Locks are always fresh; snapshots must never share synchronization primitives.
        values.pop("state_lock", None)
        values.pop("bundle_lock", None)
        page_count = file.page_count
        object.__setattr__(self, "spec", JobSpec(
            job_id, created_at, config_file, replace(file, page_count=None), **spec_values
        ))
        state_values.setdefault("page_count", page_count)
        object.__setattr__(self, "state", JobState(**state_values))
        object.__setattr__(self, "paths", JobPaths(job_id, file.stored_name))
        if values:
            raise TypeError(f"Unexpected job fields: {', '.join(values)}")

    def __getattr__(self, name: str) -> Any:
        if name == "file":
            return replace(self.spec.file, page_count=self.state.page_count)
        if name in self._SPEC_FIELDS:
            return getattr(self.spec, name)
        if name in self._STATE_FIELDS:
            return getattr(self.state, name)
        if name == "state_lock":
            return self.state.lock
        if name in {"base_path", "input_path", "output_dir", "web_path", "log_path", "meta_path", "bundle_path"}:
            return getattr(self.paths, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {"spec", "state", "paths"}:
            object.__setattr__(self, name, value)
        elif name == "file":
            object.__setattr__(self, "spec", replace(self.spec, file=replace(value, page_count=None)))
            object.__setattr__(self.state, "page_count", value.page_count)
        elif name == "page_count":
            object.__setattr__(self.state, "page_count", value)
        elif name in self._SPEC_FIELDS:
            object.__setattr__(self, "spec", replace(self.spec, **{name: value}))
        elif name in self._STATE_FIELDS:
            object.__setattr__(self.state, name, value)
        elif name == "state_lock":
            object.__setattr__(self.state, "lock", value)
        elif name == "bundle_lock":
            object.__setattr__(self.state, "bundle_lock", value)
        else:
            object.__setattr__(self, name, value)

    def __copy__(self) -> "JobRecord":
        '''Create a detached state snapshot with independent synchronization.'''
        import copy
        state_values = {
            name: copy.deepcopy(getattr(self.state, name))
            for name in self._STATE_FIELDS
            if name not in {"lock", "bundle_lock"}
        }
        return JobRecord(
            self.job_id, self.created_at, self.config_file,
            copy.deepcopy(self.file),
            **{name: getattr(self, name) for name in self._SPEC_FIELDS if name not in {
                "job_id", "created_at", "config_file", "file"
            }},
            **state_values,
        )

    @property
    def state_lock(self) -> threading.RLock:
        return self.state.lock

    @property
    def bundle_lock(self) -> threading.Lock:
        return self.state.bundle_lock

    @property
    def initially_secured(self) -> bool:
        with self.state_lock:
            if self.result is not None and self.result.initially_secured is not None:
                return self.result.initially_secured
            return any(
                stage.get("name") == "unlock" and stage.get("status") == "ok"
                for stage in self.stages
            )

    @property
    def validation_requirement(self) -> str:
        profiles = self.required_profiles()
        if profiles == ("wcag",):
            return "wcag only"
        if profiles == ("ua1",):
            return "pdfua1 only"
        if profiles == ("wcag", "ua1"):
            return "wcag and pdfua1"
        return "no validation profile"

    def artifact(self, name: str) -> Path | None:
        with self.state_lock:
            return artifact_path(
                self.output_dir, name,
                self.result.output_pdf_path if self.result else None,
            )

    def is_terminal(self) -> bool:
        with self.state_lock:
            return self.status in TERMINAL_STATUSES

    def required_profiles(self) -> tuple[str, ...]:
        return selected_validation_profiles(
            self.require_wcag, self.require_pdfua1, self.wcag_and_ua1_must_pass
        )

    def to_dict(self) -> dict[str, Any]:
        '''Compatibility serializer; API endpoints use explicit Pydantic schemas.'''
        return JobSerializer.from_record(self)


# Transition alias for the runner/store APIs while callers adopt JobRecord.
Job = JobRecord
