'''
Package a finished job's PDFs and reports into a single downloadable ZIP.
'''

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from .harvest import latest_report_folder
from .models import Job, outcome_label

MAX_ZIP_ENTRY_NAME = 120


def build_manifest(job: Job) -> dict[str, Any]:
    '''
    Describe the job and its per-file outcomes for the archive.
    '''
    payload = job.to_dict()
    payload["files"] = [
        {
            **uploaded_file.to_dict(),
            "outcome": _outcome_for(job, uploaded_file.file_id),
        }
        for uploaded_file in job.files
    ]
    return payload


def build_bundle(job: Job, destination: Path) -> Path:
    '''
    Write the downloadable ZIP for a job, replacing any cached copy atomically.
    '''
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_name(f"{destination.name}.partial")

    with zipfile.ZipFile(
        temporary_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        archive.writestr(
            f"{job.job_id}/manifest.json",
            json.dumps(build_manifest(job), indent=2, default=str)
        )

        if job.log_path.is_file():
            archive.write(job.log_path, f"{job.job_id}/pipeline.log")

        _add_files(archive, job)
        _add_reports(archive, job)

    temporary_path.replace(destination)
    return destination


def _add_files(archive: zipfile.ZipFile, job: Job) -> None:
    '''
    Add each file's remediated PDF and normalized reports.
    '''
    for uploaded_file in job.files:
        result = job.find_result(uploaded_file.file_id)
        folder = f"{job.job_id}/files/{_entry_name(uploaded_file.original_name)}"

        if result is not None and result.final_pdf_path is not None:
            if result.final_pdf_path.is_file():
                archive.write(
                    result.final_pdf_path,
                    f"{folder}/{_entry_name(uploaded_file.original_name)}"
                )

        result_folder = job.result_folder(uploaded_file.file_id)
        for report_name in ("before.json", "after.json"):
            report_path = result_folder / report_name
            if report_path.is_file():
                archive.write(report_path, f"{folder}/{report_name}")


def _add_reports(archive: zipfile.ZipFile, job: Job) -> None:
    '''
    Add the complete veraPDF report folders for both validation passes.
    '''
    report_folders = (
        ("before", latest_report_folder(
            job.workspace_path / "active" / "reports", "pre-fix"
        )),
        ("after", latest_report_folder(job.workspace_path / "reports", "full")),
    )

    for report_key, report_folder in report_folders:
        if report_folder is None or not report_folder.is_dir():
            continue
        for path in sorted(report_folder.rglob("*")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(report_folder).as_posix()
            archive.write(
                path,
                f"{job.job_id}/reports/{report_key}/{relative_path}"
            )


def _outcome_for(job: Job, file_id: str) -> dict[str, str] | None:
    '''
    Return a readable outcome label for one file.
    '''
    result = job.find_result(file_id)
    if result is None:
        return None
    return {
        "key": result.outcome,
        "label": outcome_label(result.outcome),
    }


def _entry_name(name: str) -> str:
    '''
    Return a ZIP-safe entry name derived from an uploaded filename.
    '''
    safe = Path(name).name.replace("/", "_").replace("\\", "_")
    return safe[:MAX_ZIP_ENTRY_NAME] or "file.pdf"
