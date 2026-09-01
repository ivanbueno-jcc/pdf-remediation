'''
The single-PDF remediation sequence.

This is go.py's pipeline with the batch machinery removed. Where the original
records a decision by moving a file into a folder and re-reading it later, this
keeps the decision in a local variable: validation results choose the next
stage directly.

go.py's step 5 (reprocess) has no equivalent here. It exists only to sweep files
between workspace folders, which is work this design does not create.
'''

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .capabilities import Capabilities, cached_probe, configuration_exists
from .models import (
    FONT_ISSUE_CLAUSES,
    MISSING_UNICODE_CLAUSE,
    PipelineOptions,
    PipelineResult,
    PipelineStatus,
    StageOutcome,
    StageStatus,
)
from .scratch import Scratch, replace_output_file, scratch_workspace
from . import stages

EventHandler = Callable[[StageOutcome], None]
CancelCheck = Callable[[], bool]


class PipelineCancelled(Exception):
    '''
    Raised when a caller cancels a run between stages.
    '''


class _Run:
    '''
    Mutable bookkeeping for one pipeline run.
    '''

    def __init__(
            self,
            input_pdf: Path,
            options: PipelineOptions,
            on_event: EventHandler | None,
            should_cancel: CancelCheck | None) -> None:
        '''
        Start recording a run.
        '''
        self.input_pdf = input_pdf
        self.options = options
        self.on_event = on_event
        self.should_cancel = should_cancel
        self.stages: list[StageOutcome] = []
        self.warnings: list[str] = []

    def check_cancelled(self) -> None:
        '''
        Stop the run if the caller has asked for cancellation.
        '''
        if self.should_cancel is not None and self.should_cancel():
            raise PipelineCancelled()

    def record(self, name: str, status: StageStatus, detail: str | None = None,
               started_at: datetime | None = None) -> StageOutcome:
        '''
        Record a finished stage and notify the caller.
        '''
        outcome = StageOutcome(
            name=name,
            status=status,
            detail=detail,
            started_at=started_at or datetime.now(),
            completed_at=datetime.now(),
        )
        self.stages.append(outcome)
        if self.on_event is not None:
            self.on_event(outcome)
        return outcome


def process_pdf(  # pylint: disable=too-many-return-statements,too-many-arguments,too-many-positional-arguments
        input_pdf: Path | str,
        output_dir: Path | str,
        options: PipelineOptions | None = None,
        on_event: EventHandler | None = None,
        should_cancel: CancelCheck | None = None) -> PipelineResult:
    '''
    Remediate one PDF and return it with a before and after validation report.

    The input file is never modified. Everything intermediate lives in a
    throwaway directory that is removed before this returns.
    '''
    options = options or PipelineOptions()
    input_path = Path(input_pdf).expanduser().resolve()
    output_directory = Path(output_dir).expanduser().resolve()
    run = _Run(input_path, options, on_event, should_cancel)

    invalid = _validate_inputs(input_path, options)
    if invalid is not None:
        return PipelineResult(
            status=PipelineStatus.FAILED, input_pdf_path=input_path, error=invalid
        )

    capabilities = cached_probe()
    if not capabilities.can_validate():
        return PipelineResult(
            status=PipelineStatus.FAILED,
            input_pdf_path=input_path,
            error=(
                "Validation is unavailable: "
                f"java={capabilities.detail['java']}, "
                f"veraPDF={capabilities.detail['verapdf_jar']}. "
                "Without it every file would be reported as unvalidatable."
            ),
        )

    output_directory.mkdir(parents=True, exist_ok=True)
    try:
        with scratch_workspace() as scratch:
            return _run_sequence(run, scratch, capabilities, output_directory)
    except PipelineCancelled:
        return PipelineResult(
            status=PipelineStatus.CANCELLED,
            input_pdf_path=input_path,
            stages=run.stages,
            warnings=run.warnings,
            error="Cancelled.",
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        return PipelineResult(
            status=PipelineStatus.FAILED,
            input_pdf_path=input_path,
            stages=run.stages,
            warnings=run.warnings,
            error=f"{type(error).__name__}: {error}",
        )


def _validate_inputs(input_path: Path, options: PipelineOptions) -> str | None:
    '''
    Return an error message when the request cannot be honoured.
    '''
    if not input_path.is_file():
        return f"Input PDF not found: {input_path}"
    if input_path.suffix.lower() != ".pdf":
        return f"Input file must use a .pdf extension: {input_path}"
    if not configuration_exists(options.config_file):
        return (
            "Configuration not found under resources/configuration: "
            f"{options.config_file}"
        )
    return None


def _run_sequence(
        run: _Run,
        scratch: Scratch,
        capabilities: Capabilities,
        output_directory: Path) -> PipelineResult:
    '''
    Execute the stages in order, branching on validation results.
    '''
    options = run.options
    name = run.input_pdf.name

    # 1. Validate: the before report, and the basis for every later decision.
    started = datetime.now()
    before = stages.validate(run.input_pdf)
    run.record("validate_before", StageStatus.OK, _describe(before), started)

    if before.get("status") == "error":
        return _finish(run, scratch, PipelineStatus.FAILED, None, before, before,
                       error="veraPDF could not validate this file.")

    # 2. Already compliant: return it untouched rather than rewriting it.
    if stages.meets_compliance_gate(before, options.wcag_and_ua1_must_pass):
        run.record("compliance_gate", StageStatus.OK, "Already compliant; no changes made.")
        destination = output_directory / name
        shutil.copy2(run.input_pdf, destination)
        return _finish(run, scratch, PipelineStatus.ALREADY_COMPLIANT,
                       destination, before, before)

    current = run.input_pdf

    # 3. Unlock, so encryption does not end the run the way it does in batch mode.
    current = _maybe_unlock(run, scratch, current, name)
    run.check_cancelled()

    # 4. Fix with the requested configuration.
    if options.attempt_fix:
        started = datetime.now()
        current = stages.run_fix(scratch, current, options.config_file, options, name)
        run.record("fix", StageStatus.OK, f"Applied {options.config_file}.", started)
    else:
        run.record("fix", StageStatus.SKIPPED, "Disabled by request.")
    run.check_cancelled()

    report = stages.validate(current)

    # 5. Font repair, when the failures are font failures and Docker is up.
    current, report = _maybe_font_fix(run, scratch, capabilities, current, report, name)
    run.check_cancelled()

    # 6. Targeted configs for specific remaining clause-tests.
    current, report = _maybe_targeted_fixes(run, scratch, current, report, name)
    run.check_cancelled()

    # 7. Validate: the after report.
    started = datetime.now()
    after = report
    run.record("validate_after", StageStatus.OK, _describe(after), started)

    destination = output_directory / name
    replace_output_file(current, destination)
    return _finish(run, scratch, _outcome_status(before, after), destination, before, after)


def _maybe_unlock(run: _Run, scratch: Scratch, current: Path, name: str) -> Path:
    '''
    Strip empty-password encryption when present and permitted.
    '''
    started = datetime.now()
    status = stages.is_secured(current)

    if status == "pdfix-unable-to-open":
        raise RuntimeError("PDFix could not open this PDF.")
    if status == "unsecured":
        run.record("unlock", StageStatus.SKIPPED, "Not encrypted.", started)
        return current
    if not run.options.attempt_unlock:
        raise RuntimeError(f"PDF is encrypted ({status}) and unlocking is disabled.")

    unlocked_path = scratch.output_path(f"unlocked-{name}")
    result = stages.unlock(current, unlocked_path)
    if result.get("status") == "error":
        raise RuntimeError(
            f"Could not remove security: {result.get('error', 'unknown error')}"
        )

    for warning in result.get("warnings", []):
        run.warnings.append(str(warning))
    run.record("unlock", StageStatus.OK, f"Security removed ({status}).", started)
    return unlocked_path


def _maybe_font_fix(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        run: _Run,
        scratch: Scratch,
        capabilities: Capabilities,
        current: Path,
        report: dict[str, Any],
        name: str) -> tuple[Path, dict[str, Any]]:
    '''
    Run the Callas and PDFix font stages when the failures call for them.
    '''
    if not run.options.attempt_font_fix:
        run.record("font_fix", StageStatus.SKIPPED, "Disabled by request.")
        return current, report

    if not FONT_ISSUE_CLAUSES & stages.failing_clauses(report):
        run.record("font_fix", StageStatus.SKIPPED, "No font clauses failing.")
        return current, report

    if not capabilities.can_font_fix_callas():
        run.record(
            "font_fix_callas", StageStatus.SKIPPED,
            f"Unavailable: docker={capabilities.detail['docker']}, "
            f"callas={capabilities.detail['callas_licence']}.",
        )
    else:
        started = datetime.now()
        try:
            current = stages.run_callas_font_fix(scratch, current, name)
            report = stages.validate(current)
            run.record("font_fix_callas", StageStatus.OK, _describe(report), started)
        except Exception as error:  # pylint: disable=broad-exception-caught
            run.record(
                "font_fix_callas", StageStatus.FAILED,
                f"{type(error).__name__}: {error}", started,
            )

    run.check_cancelled()

    # The PDFix step exists only for the residual missing-unicode failures.
    if MISSING_UNICODE_CLAUSE not in stages.failing_clauses(report):
        run.record("font_fix_pdfix", StageStatus.SKIPPED,
                   f"Clause {MISSING_UNICODE_CLAUSE} not failing.")
        return current, report

    if not capabilities.can_font_fix_pdfix():
        run.record(
            "font_fix_pdfix", StageStatus.SKIPPED,
            f"Unavailable: docker={capabilities.detail['docker']}, "
            f"pdfix={capabilities.detail['pdfix_licence']}.",
        )
        return current, report

    started = datetime.now()
    try:
        current = stages.run_pdfix_font_fix(scratch, current, name)
        report = stages.validate(current)
        run.record("font_fix_pdfix", StageStatus.OK, _describe(report), started)
    except Exception as error:  # pylint: disable=broad-exception-caught
        run.record("font_fix_pdfix", StageStatus.FAILED,
                   f"{type(error).__name__}: {error}", started)
    return current, report


def _maybe_targeted_fixes(
        run: _Run,
        scratch: Scratch,
        current: Path,
        report: dict[str, Any],
        name: str) -> tuple[Path, dict[str, Any]]:
    '''
    Apply the clause-test specific configurations that still match.
    '''
    if not run.options.attempt_targeted_fixes:
        run.record("fix_target", StageStatus.SKIPPED, "Disabled by request.")
        return current, report

    actions, matched = stages.matching_target_actions(report, run.options.targets)
    if not actions:
        run.record("fix_target", StageStatus.SKIPPED, "No targeted clause-tests failing.")
        return current, report

    started = datetime.now()
    try:
        current = stages.run_targeted_fixes(scratch, current, actions, name)
        report = stages.validate(current)
        run.record(
            "fix_target", StageStatus.OK,
            f"Matched {', '.join(matched)}; applied {', '.join(actions)}.", started,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        run.record("fix_target", StageStatus.FAILED,
                   f"{type(error).__name__}: {error}", started)
    return current, report


def _outcome_status(before: dict[str, Any], after: dict[str, Any]) -> PipelineStatus:
    '''
    Describe what the run achieved, comparing the two reports.
    '''
    if after.get("status") == "error":
        return PipelineStatus.FAILED
    if after.get("passed"):
        return PipelineStatus.REMEDIATED

    before_count = int(before.get("failed_rules_count") or 0)
    after_count = int(after.get("failed_rules_count") or 0)
    if after_count < before_count:
        return PipelineStatus.IMPROVED
    return PipelineStatus.UNCHANGED


def _finish(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        run: _Run,
        scratch: Scratch,
        status: PipelineStatus,
        output_path: Path | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        error: str | None = None) -> PipelineResult:
    '''
    Assemble the result, folding in whatever the utilities recorded.

    The reports are written next to the PDF as well as returned: a caller asked
    for three artifacts, so all three should exist on disk.
    '''
    if output_path is not None:
        write_reports(output_path.parent, before, after)

    return PipelineResult(
        status=status,
        input_pdf_path=run.input_pdf,
        output_pdf_path=output_path,
        before=before,
        after=after,
        stages=run.stages,
        warnings=run.warnings,
        diagnostics=scratch.collect_diagnostics(),
        error=error,
    )


def write_reports(
        output_dir: Path,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None) -> None:
    '''
    Write the before and after reports beside the remediated PDF.
    '''
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, report in (("before.json", before), ("after.json", after)):
        path = output_dir / name
        if report is None:
            path.unlink(missing_ok=True)
            continue
        path.write_text(
            json.dumps(report, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )


def artifact_path(
        output_dir: Path,
        name: str,
        output_pdf_path: Path | None) -> Path | None:
    '''
    Return one of the three artifacts a run produces, if it exists.

    Paired with write_reports: one decides where they go, the other finds them,
    so callers never encode the layout themselves.
    '''
    candidates: dict[str, Path | None] = {
        "pdf": output_pdf_path,
        "before": output_dir / "before.json",
        "after": output_dir / "after.json",
    }
    candidate = candidates.get(name)
    if candidate is None or not Path(candidate).is_file():
        return None
    return Path(candidate)


def _describe(report: dict[str, Any]) -> str:
    '''
    Summarize a validation report in one line.
    '''
    profiles = report.get("profiles", {})
    parts = [
        f"{key.upper()} {value.get('status')}"
        + (f"/{value.get('failed_rules_count')}" if value.get("failed_rules_count") else "")
        for key, value in profiles.items()
    ]
    return ", ".join(parts) or str(report.get("status", "unknown"))
