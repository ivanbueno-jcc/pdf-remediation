'''Job metadata persistence, restart recovery, and retention policy.'''

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pdf_api.models import PipelineResult, PipelineStatus

from ..config import JOBS_ROOT, job_ttl_hours
from ..identity import legacy_job_owner, normalize_user
from ..models import JobPaths, JobRecord, JobSpec, JobState, JobStatus, UploadedFile

if TYPE_CHECKING:
    from ..store import JobStore

_JOB_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


def is_valid_job_id(job_id: str) -> bool:
    '''Return whether a job ID matches the filesystem-safe identifier format.'''
    return bool(_JOB_ID_PATTERN.match(job_id or ""))


def save_meta(job: JobRecord) -> None:
    '''Atomically persist a consistent job response and pipeline output path.'''
    with job.state.lock:
        job.paths.web_path.mkdir(parents=True, exist_ok=True)
        payload = job.to_dict()
        result = job.state.result
        payload["output_pdf_path"] = (
            result.output_pdf_path.relative_to(job.paths.base_path).as_posix()
            if result and result.output_pdf_path
            and result.output_pdf_path.is_relative_to(job.paths.base_path)
            else None
        )
        metadata_path = job.paths.meta_path
        serialized = json.dumps(payload, indent=2, default=str)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=job.paths.web_path,
                prefix=f"{metadata_path.name}.", suffix=".partial", delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(metadata_path)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def load_meta(meta_path: Path) -> JobRecord | None:
    '''Load one metadata record, rejecting malformed and legacy batch records.'''
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    job_id = payload.get("job_id", "")
    file_payload = payload.get("file")
    if not is_valid_job_id(job_id) or not isinstance(file_payload, dict):
        return None

    legacy_both = bool(payload.get("wcag_and_ua1_must_pass"))
    if "require_wcag" in payload or "require_pdfua1" in payload:
        require_wcag = bool(payload.get("require_wcag"))
        require_pdfua1 = bool(payload.get("require_pdfua1"))
    else:
        require_wcag, require_pdfua1 = True, legacy_both

    stored_name = file_payload.get("stored_name", "")
    spec = JobSpec(
        job_id=job_id,
        created_at=_parse_datetime(payload.get("created_at")),
        config_file=payload.get("config_file", ""),
        file=UploadedFile(
            original_name=file_payload.get("original_name", ""),
            stored_name=stored_name,
            size_bytes=int(file_payload.get("size_bytes") or 0),
            page_count=(int(file_payload["page_count"])
                        if file_payload.get("page_count") is not None else None),
        ),
        submitted_by=normalize_user(payload.get("submitted_by")) or (legacy_job_owner() or ""),
        attempt_unlock=bool(payload.get("attempt_unlock", True)),
        attempt_fix=bool(payload.get("attempt_fix", True)),
        skip_font_fix=bool(payload.get("skip_font_fix")),
        attempt_targeted_fixes=bool(payload.get("attempt_targeted_fixes", True)),
        wcag_and_ua1_must_pass=legacy_both,
        require_wcag=require_wcag,
        require_pdfua1=require_pdfua1,
        verbose=bool(payload.get("verbose")),
    )
    state = JobState(
        status=_parse_status(payload.get("status")),
        outcome=payload.get("outcome"),
        error=payload.get("error"),
        started_at=_parse_optional_datetime(payload.get("started_at")),
        finished_at=_parse_optional_datetime(payload.get("finished_at")),
        stages=list(payload.get("stages") or []),
        page_count=spec.file.page_count,
        has_pdf=False,
    )
    jobs_root = meta_path.parent.parent.parent
    job = JobRecord(spec, state, JobPaths(job_id, stored_name, jobs_root))
    output_relative = payload.get("output_pdf_path")
    state.result = PipelineResult(
        status=_parse_pipeline_status(payload.get("outcome")),
        input_pdf_path=job.paths.input_path,
        output_pdf_path=job.paths.base_path / output_relative if output_relative else None,
        before=_read_report(job.paths.output_dir / "before.json"),
        after=_read_report(job.paths.output_dir / "after.json"),
        initially_secured=(payload.get("initially_secured")
                           if isinstance(payload.get("initially_secured"), bool) else None),
        warnings=list(payload.get("warnings") or []),
        diagnostics=list(payload.get("diagnostics") or []),
        error=payload.get("error"),
    )
    state.has_pdf = job.artifact("pdf") is not None
    return job


def load_persisted_jobs(
        store: "JobStore", jobs_root: Path | None = None) -> tuple[int, int]:
    '''Recover jobs at startup and report those with no discoverable owner.'''
    jobs_root = jobs_root or JOBS_ROOT
    if not jobs_root.is_dir():
        return 0, 0
    loaded = unowned = 0
    for job_path in sorted(jobs_root.iterdir()):
        if not job_path.is_dir() or not is_valid_job_id(job_path.name):
            continue
        meta_path = job_path / "_web" / "meta.json"
        if not meta_path.is_file():
            continue
        job = load_meta(meta_path)
        if job is None:
            continue
        if not job.is_terminal():
            job.state.status = JobStatus.FAILED
            job.state.error = job.state.error or "Server restarted while this job was running."
        if not job.spec.submitted_by:
            unowned += 1
        store.add(job)
        loaded += 1
    return loaded, unowned


def sweep_expired_jobs(
        store: "JobStore", jobs_root: Path | None = None) -> int:
    '''Remove expired terminal job directories under the artifact lock.'''
    jobs_root = jobs_root or JOBS_ROOT
    ttl_hours = job_ttl_hours()
    if ttl_hours <= 0 or not jobs_root.is_dir():
        return 0
    cutoff = datetime.now() - timedelta(hours=ttl_hours)
    removed = 0
    for job_path in sorted(jobs_root.iterdir()):
        if not job_path.is_dir() or not is_valid_job_id(job_path.name):
            continue
        job = store.get(job_path.name)
        if job is not None and not job.is_terminal():
            continue
        try:
            if datetime.fromtimestamp(job_path.stat().st_mtime) > cutoff:
                continue
        except OSError:
            continue
        with store.job_artifact_lock(job_path.name) as exists:
            live_job = store.get_mutable(job_path.name) if exists else None
            if live_job is not None and not live_job.is_terminal():
                continue
            try:
                shutil.rmtree(job_path)
            except OSError:
                continue
            store.remove(job_path.name)
            removed += 1
    return removed


def _read_report(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_pipeline_status(value: object) -> PipelineStatus:
    try:
        return PipelineStatus(str(value))
    except ValueError:
        return PipelineStatus.FAILED


def _parse_status(value: object) -> JobStatus:
    try:
        return JobStatus(str(value))
    except ValueError:
        return JobStatus.FAILED


def _parse_datetime(value: object) -> datetime:
    return _parse_optional_datetime(value) or datetime.now()


def _parse_optional_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
