'''
Individual stages of the single-PDF pipeline.

Each stage takes a PDF, does one thing, and returns the path to its output plus
an outcome record. Stages never move files between folders to signal a decision;
the sequence in pipeline.py makes those decisions from validation results.
'''

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any

from pdf_remediation.fix import fix_with_process_timeout
from pdf_remediation.fix_target import get_matching_actions, remediate_target_file
from pdf_remediation.utilities.callas import Callas
from pdf_remediation.utilities.pdfix import font_fix_pdfix, is_pdf_secured
from pdf_worker.solo_remove_security import remove_security
from pdf_worker.solo_validate import validate_pdf

from .models import PipelineOptions
from .scratch import Scratch


@contextlib.contextmanager
def quiet():
    '''
    Keep the batch utilities' console output off stdout.

    They print progress banners and errors unconditionally. Redirecting to
    stderr keeps stdout clean for callers that emit JSON there, and stops the
    chatter from being mistaken for our own output.
    '''
    with contextlib.redirect_stdout(sys.stderr):
        yield


def validate(pdf_path: Path) -> dict[str, Any]:
    '''
    Validate one PDF against UA1 and WCAG.

    Returns solo_validate's shape, which already carries the clause_test ids
    that the targeted-fix stage matches on.
    '''
    with quiet():
        return validate_pdf(str(pdf_path))


def meets_compliance_gate(report: dict[str, Any], wcag_and_ua1_must_pass: bool) -> bool:
    '''
    Return whether a report satisfies the configured compliance gate.

    Mirrors go.py's validation_passed_required_compliance: WCAG alone by
    default, both profiles when the caller demands it.
    '''
    profiles = report.get("profiles", {})
    wcag_passed = bool(profiles.get("wcag", {}).get("passed"))
    if not wcag_and_ua1_must_pass:
        return wcag_passed
    return wcag_passed and bool(profiles.get("ua1", {}).get("passed"))


def failing_clauses(report: dict[str, Any]) -> set[str]:
    '''
    Return the bare clause numbers a report reports as failing.
    '''
    clauses: set[str] = set()
    for profile in report.get("profiles", {}).values():
        for violation in profile.get("violations", []):
            clause = str(violation.get("clause", "")).strip()
            if clause:
                clauses.add(clause)
    return clauses


def failing_clause_tests(report: dict[str, Any]) -> set[str]:
    '''
    Return the clause-test ids a report reports as failing, e.g. "7.1-9".
    '''
    clause_tests: set[str] = set()
    for profile in report.get("profiles", {}).values():
        for violation in profile.get("violations", []):
            clause_test = str(violation.get("clause_test", "")).strip()
            if clause_test:
                clause_tests.add(clause_test)
    return clause_tests


def is_secured(pdf_path: Path) -> str:
    '''
    Return the security status of a PDF.

    is_pdf_secured is annotated as returning a bool but actually returns a
    single-entry mapping of path to status string.
    '''
    with quiet():
        status = is_pdf_secured(str(pdf_path))
    if isinstance(status, dict):
        return str(next(iter(status.values()), "unsecured"))
    return "unsecured"


def unlock(pdf_path: Path, output_path: Path) -> dict[str, Any]:
    '''
    Remove empty-password security from a PDF, leaving the input untouched.
    '''
    with quiet():
        return remove_security(str(pdf_path), str(output_path))


def run_fix(
        scratch: Scratch,
        pdf_path: Path,
        config_file: str,
        options: PipelineOptions,
        reported_name: str) -> Path:
    '''
    Apply one PDFix configuration, returning the remediated PDF.

    The input is staged first because PDFix deletes the file it is given.
    '''
    staged_input = scratch.stage_input(pdf_path, name=reported_name)
    output_path = scratch.output_path(reported_name)
    output_path.unlink(missing_ok=True)

    with quiet():
        fix_with_process_timeout(
            str(staged_input),
            str(output_path),
            config_file,
            scratch.files,
            False,
            reported_input_pdf_path=reported_name,
            process_timeout=options.fix_timeout_seconds,
        )

    if not output_path.is_file():
        raise RuntimeError(f"PDFix produced no output for {reported_name}.")
    return output_path


def run_callas_font_fix(scratch: Scratch, pdf_path: Path, reported_name: str) -> Path:
    '''
    Run the Callas font fix in Docker, returning the repaired PDF.
    '''
    staged_input = scratch.stage_input(pdf_path, name=reported_name)
    output_path = scratch.output_path(reported_name)
    output_path.unlink(missing_ok=True)

    with quiet():
        Callas.font_fix(staged_input, output_path, scratch.workspace)

    if not output_path.is_file():
        raise RuntimeError(f"Callas produced no output for {reported_name}.")
    return output_path


def run_pdfix_font_fix(scratch: Scratch, pdf_path: Path, reported_name: str) -> Path:
    '''
    Run the PDFix missing-unicode font fix in Docker.

    This one deletes its input whether or not it succeeds, which is safe only
    because the input is a staged copy.
    '''
    staged_input = scratch.stage_input(pdf_path, name=reported_name)
    output_path = scratch.output_path(reported_name)
    output_path.unlink(missing_ok=True)

    with quiet():
        font_fix_pdfix(staged_input, output_path, scratch.workspace)

    if not output_path.is_file():
        raise RuntimeError(f"PDFix font fix produced no output for {reported_name}.")
    return output_path


def matching_target_actions(
        report: dict[str, Any],
        targets: tuple[tuple[str, str], ...]) -> tuple[list[str], list[str]]:
    '''
    Return the configs to apply for a report's failing clause-tests.

    Reuses fix_target's matcher so ordering and de-duplication stay identical:
    a file failing both 5-1 and 7.1-9 runs restore_metadata.json once.
    '''
    matched_clause_tests, matched_actions = get_matching_actions(
        failing_clause_tests(report), list(targets)
    )
    return list(matched_actions), list(matched_clause_tests)


def run_targeted_fixes(
        scratch: Scratch,
        pdf_path: Path,
        action_names: list[str],
        reported_name: str) -> Path:
    '''
    Apply a chain of targeted configs, each feeding the next.
    '''
    staged_input = scratch.stage_input(pdf_path, name=reported_name)
    output_path = scratch.output_path(reported_name)
    output_path.unlink(missing_ok=True)

    with quiet():
        result = remediate_target_file(
            str(staged_input),
            str(output_path),
            tuple(action_names),
            scratch.files,
            scratch.staging,
            False,
        )

    if not result.get("success"):
        raise RuntimeError(result.get("error") or "Targeted remediation failed.")
    if not output_path.is_file():
        raise RuntimeError(f"Targeted remediation produced no output for {reported_name}.")
    return output_path
