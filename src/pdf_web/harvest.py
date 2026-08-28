'''
Map uploaded PDFs onto the files and reports the pipeline left behind.
'''

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pdf_remediation.utilities.verapdf import parseValidationReport
from pdf_worker.solo_validate import with_clause_test

from .config import REPO_ROOT
from .models import FileResult, Job, JobStatus, outcome_label

PROFILES = ("ua1", "wcag")

FINAL_FOLDER_PRIORITY = (
    "remediated",
    "font-issues",
    "font-issues-missing-unicode",
    "unable-to-validate",
    "secured-needs-approval",
    "secured-cannot-process",
    "pdfix-unable-to-open",
    "pdfix-cannot-process",
    "unable-to-process",
    "active",
)

FINAL_DIRECTORIES = ("files", "processed")

# Mirrors validate.FULL_VALIDATION_IGNORED_SUBFOLDERS: files routed here are
# never included in the final --full validation, so they get no after report.
FULL_VALIDATION_EXCLUDED = frozenset({
    "unable-to-validate",
    "secured-needs-approval",
    "secured-cannot-process",
    "pdfix-unable-to-open",
    "pdfix-cannot-process",
    "unable-to-process",
})

def harvest_job(job: Job) -> list[FileResult]:
    '''
    Build a per-file result set from the finished workspace.
    '''
    before_report = latest_report_folder(
        job.workspace_path / "active" / "reports",
        "pre-fix"
    )
    after_report = latest_report_folder(job.workspace_path / "reports", "full")

    before_index = load_results_index(before_report)
    after_index = load_results_index(after_report)

    results: list[FileResult] = []
    for uploaded_file in job.files:
        results.append(build_file_result(
            job,
            uploaded_file.file_id,
            uploaded_file.stored_name,
            before_report,
            before_index,
            after_report,
            after_index,
        ))

    job.results = results
    return results


def build_file_result(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        job: Job,
        file_id: str,
        stored_name: str,
        before_report: Path | None,
        before_index: dict[str, dict[str, str]],
        after_report: Path | None,
        after_index: dict[str, dict[str, str]]) -> FileResult:
    '''
    Assemble one uploaded file's outcome, before report, and after report.
    '''
    located = find_final_pdf(job.workspace_path, stored_name)
    outcome = located[0] if located else "missing"
    final_pdf_path = located[1] if located else None

    staged_input_path = job.workspace_path / "active" / "files" / stored_name
    before = build_stage_report(
        before_report,
        before_index.get(stored_name),
        staged_input_path,
        stored_name,
        "before"
    )
    after = build_stage_report(
        after_report,
        after_index.get(stored_name),
        final_pdf_path,
        stored_name,
        "after"
    )

    notes = [
        describe_outcome(outcome, job.status == JobStatus.COMPLETED),
        describe_missing_after(outcome, after, after_report),
    ]
    note = " ".join(part for part in notes if part) or None
    write_stage_reports(job, file_id, before, after)

    return FileResult(
        file_id=file_id,
        outcome=outcome,
        final_pdf_path=final_pdf_path,
        before=before,
        after=after,
        note=note,
    )


def describe_missing_after(
        outcome: str,
        after: dict[str, Any] | None,
        after_report: Path | None) -> str | None:
    '''
    Explain why a file has no final validation report.
    '''
    if after is not None:
        return None
    if outcome in FULL_VALIDATION_EXCLUDED:
        return f"Excluded from final validation: {outcome_label(outcome)}."
    if after_report is None:
        return "Final validation did not run."
    return "No final validation result was recorded for this file."


def describe_outcome(outcome: str, job_is_complete: bool) -> str | None:
    '''
    Explain an outcome that is easy to misread.
    '''
    if outcome == "active":
        if job_is_complete:
            return "Remediation ran but the file still fails validation."
        return "The pipeline stopped before this file was routed."
    if outcome == "missing":
        return "The pipeline produced no output for this file."
    return None


def find_final_pdf(workspace_path: Path, stored_name: str) -> tuple[str, Path] | None:
    '''
    Locate an uploaded file's resting place, most successful folder first.
    '''
    for folder_name in FINAL_FOLDER_PRIORITY:
        for directory in FINAL_DIRECTORIES:
            candidate = workspace_path / folder_name / directory / stored_name
            if candidate.is_file():
                return folder_name, candidate
    return None


def latest_report_folder(reports_path: Path, suffix: str) -> Path | None:
    '''
    Return the newest timestamped report folder with the given suffix.
    '''
    if not reports_path.is_dir():
        return None
    folders = sorted(
        path for path in reports_path.glob(f"*-{suffix}") if path.is_dir()
    )
    return folders[-1] if folders else None


def load_results_index(report_folder: Path | None) -> dict[str, dict[str, str]]:
    '''
    Index vera_validation_results.csv rows by PDF basename.
    '''
    if report_folder is None:
        return {}
    csv_path = report_folder / "vera_validation_results.csv"
    if not csv_path.is_file():
        return {}
    try:
        with csv_path.open(newline="", encoding="utf-8") as handle:
            return {
                PurePosixPath(row.get("path", "")).name: row
                for row in csv.DictReader(handle)
                if row.get("path")
            }
    except OSError:
        return {}


def status_from_csv_value(value: object) -> str:
    '''
    Normalize a veraPDF status recorded in the results CSV.
    '''
    text = str(value or "").strip()
    if text.upper() == "TRUE":
        return "pass"
    if text.upper() == "FALSE":
        return "fail"
    if text.lower() == "error":
        return "error"
    return "unknown"


def expected_xml_name(pdf_path: Path) -> str:
    '''
    Rebuild the flattened XML report filename veraPDF wrote for a PDF path.
    '''
    parent = pdf_path.parent.as_posix().replace("/", "-")
    return f"{parent}-{pdf_path.stem.split('.')[0]}.xml"


def as_child_path(pdf_path: Path) -> Path:
    '''
    Express a path the way the pipeline subprocess saw it.

    The child runs with the repository root as its working directory and a
    relative PROJECT_BASE_PATH, so its paths are repo-relative.
    '''
    try:
        return pdf_path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return pdf_path


def read_report_xml(
        report_folder: Path,
        profile: str,
        pdf_path: Path | None,
        stored_name: str) -> str | None:
    '''
    Read the veraPDF XML for one PDF and profile, with a suffix-match fallback.
    '''
    xml_folder = report_folder / "xml" / profile
    if not xml_folder.is_dir():
        return None

    if pdf_path is not None:
        exact_path = xml_folder / expected_xml_name(as_child_path(pdf_path))
        if exact_path.is_file():
            return _read_text(exact_path)

    # The flattened name embeds the full parent path, so anchor the fallback on
    # the parent folder as well: "-files-report" cannot match "-files-final-report".
    stem = Path(stored_name).stem.split(".")[0]
    if pdf_path is not None:
        marker = "-".join(as_child_path(pdf_path).parts[-3:-1])
        suffix = f"-{marker}-{stem}"
    else:
        suffix = f"-{stem}"

    matches = [
        path for path in xml_folder.glob("*.xml")
        if path.stem.endswith(suffix)
    ]
    if len(matches) == 1:
        return _read_text(matches[0])
    return None


def build_stage_report(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        report_folder: Path | None,
        csv_row: dict[str, str] | None,
        pdf_path: Path | None,
        stored_name: str,
        stage: str) -> dict[str, Any] | None:
    '''
    Build a solo_validate-shaped report for one PDF at one pipeline stage.

    Status comes from the results CSV rather than from XML presence, because
    veraPDF writes no XML at all when validation itself errors.
    '''
    if report_folder is None or csv_row is None:
        return None

    profiles: dict[str, Any] = {}
    for profile in PROFILES:
        status = status_from_csv_value(csv_row.get(profile))
        xml_text = read_report_xml(report_folder, profile, pdf_path, stored_name)
        profiles[profile] = {
            "status": status,
            "passed": status == "pass",
            "failed_rules_count": _read_int(csv_row.get(f"{profile}_failed_rules_count")),
            "violations": parse_violations(xml_text),
        }

    statuses = [profile["status"] for profile in profiles.values()]
    if "error" in statuses:
        overall = "error"
    elif all(status == "pass" for status in statuses):
        overall = "pass"
    else:
        overall = "fail"

    return {
        "stage": stage,
        "pdf_name": stored_name,
        "pdf_path": str(pdf_path) if pdf_path else None,
        "report_folder": report_folder.name,
        "validated_at": _report_folder_timestamp(report_folder),
        "status": overall,
        "passed": overall == "pass",
        "failed_rules_count": sum(
            profile["failed_rules_count"] for profile in profiles.values()
        ),
        "profiles": profiles,
    }


def parse_violations(xml_text: str | None) -> list[dict[str, Any]]:
    '''
    Turn a veraPDF XML report into clause-tagged violation dictionaries.
    '''
    if not xml_text:
        return []
    try:
        rules = parseValidationReport(xml_text)
    except Exception:  # pylint: disable=broad-exception-caught
        return []
    return [with_clause_test(rule) for rule in rules if isinstance(rule, dict)]


def write_stage_reports(
        job: Job,
        file_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None) -> None:
    '''
    Persist the normalized before and after reports for one file.
    '''
    folder = job.result_folder(file_id)
    folder.mkdir(parents=True, exist_ok=True)
    for name, report in (("before.json", before), ("after.json", after)):
        if report is None:
            (folder / name).unlink(missing_ok=True)
            continue
        (folder / name).write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8"
        )


def _read_text(path: Path) -> str | None:
    '''
    Read a text file, returning None when it cannot be read.
    '''
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _read_int(value: object) -> int:
    '''
    Parse an integer from CSV text, defaulting to zero.
    '''
    try:
        return int(float(str(value or 0)))
    except ValueError:
        return 0


def _report_folder_timestamp(report_folder: Path) -> str | None:
    '''
    Derive an ISO timestamp from a report folder name.
    '''
    stamp = report_folder.name.split("-", 1)[0]
    try:
        return datetime.strptime(stamp, "%Y%m%d_%H%M%S").isoformat(timespec="seconds")
    except ValueError:
        return None
