'''
Package one job's PDF and reports into a single downloadable ZIP.
'''

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from .models import Job, outcome_label

MAX_ENTRY_NAME = 120


def build_manifest(job: Job) -> dict[str, Any]:
    '''
    Describe the job and what the pipeline did, for the archive.
    '''
    payload = job.to_dict()
    payload["outcome_label"] = outcome_label(job.outcome)
    return payload


def build_bundle(job: Job, destination: Path) -> Path:
    '''
    Write the downloadable ZIP for a job, replacing any cached copy atomically.
    '''
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination.with_name(f"{destination.name}.partial")
    folder = _entry_name(Path(job.file.original_name).stem)

    with zipfile.ZipFile(
        temporary_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        archive.writestr(
            f"{folder}/manifest.json",
            json.dumps(build_manifest(job), indent=2, default=str),
        )
        if job.log_path.is_file():
            archive.write(job.log_path, f"{folder}/pipeline.log")

        pdf_path = job.artifact("pdf")
        if pdf_path is not None:
            archive.write(pdf_path, f"{folder}/{_entry_name(job.file.original_name)}")

        for artifact in ("before", "after"):
            path = job.artifact(artifact)
            if path is not None:
                archive.write(path, f"{folder}/{artifact}.json")

    temporary_path.replace(destination)
    return destination


def _entry_name(name: str) -> str:
    '''
    Return a ZIP-safe entry name derived from an uploaded filename.
    '''
    safe = Path(name).name.replace("/", "_").replace("\\", "_")
    return safe[:MAX_ENTRY_NAME] or "file"
