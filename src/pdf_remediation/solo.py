# pylint: disable=too-many-locals,too-many-statements,too-many-branches
'''
Single-PDF remediation entrypoint.

This module intentionally avoids the project/workspace routing pipeline used by
go.py, fix.py, font_fix.py, and font_fix_pdfix.py. It stages one PDF only long
enough to satisfy existing PDFix and Docker volume expectations, validates the
original once, chooses optional stages from that initial validation only, then
validates the final candidate once.
'''

from __future__ import annotations

import argparse
import json
import multiprocessing
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .fix import fix_with_process_timeout
from .fix_target import get_matching_actions, parse_target_pairs
from .utilities.callas import Callas
from .utilities.pdfix import font_fix_pdfix, pull_image
from .utilities.report import run_report_generation
from .utilities.resources import (
    CALLAS_FONT_IMAGE,
    CONFIG_DIR,
    PDFIX_FONT_IMAGE,
    get_relative_report_path,
    print_console_banner,
    print_console_key_value_rows,
    print_console_message,
    print_console_section,
)
from .utilities.verapdf import validatePdf, write_validation_report


DEFAULT_TARGETS = [
    "5-1:restore_metadata.json",
    "7.1-9:restore_metadata.json",
    "7.1-5:role_mapping_fix-7.1-5.json",
    "7.2-29:language_fix-7.2-29.json",
]

DEFAULT_CALLAS_CLAUSE_TESTS = [
    "7.21.4.1",
    "7.21.3.2",
    "7.21.4.2",
    "7.21.8",
    "7.21.7",
    "7.21.6",
    "7.21.5",
]

DEFAULT_PDFIX_FONT_CLAUSE_TESTS = ["7.21.7"]
VALIDATION_PROFILES = ["ua1", "wcag"]


class SoloError(RuntimeError):
    '''
    Operational error while processing a single PDF.
    '''


def now_iso() -> str:
    '''
    Return a local ISO-8601 timestamp for JSON summaries.
    '''
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_value_list(raw_values: list[str] | None) -> list[str]:
    '''
    Parse comma-separated and space-separated CLI values while preserving order.
    '''
    parsed_values: list[str] = []
    seen_values: set[str] = set()
    for raw_value in raw_values or []:
        for item in str(raw_value).split(","):
            parsed_item = item.strip()
            if not parsed_item or parsed_item in seen_values:
                continue
            seen_values.add(parsed_item)
            parsed_values.append(parsed_item)
    return parsed_values


def get_violation_clause_test(violation: dict) -> str:
    '''
    Return the most specific clause-test identifier available for one violation.
    '''
    clause_test = str(violation.get("clause_test", "")).strip()
    if clause_test:
        return clause_test

    clause = str(violation.get("clause", "")).strip()
    if not clause:
        return ""

    test_number = str(
        violation.get("test")
        or violation.get("testNumber")
        or violation.get("testNo")
        or violation.get("testno")
        or ""
    ).strip()
    return f"{clause}-{test_number}" if test_number else clause


def get_violation_clause(violation: dict) -> str:
    '''
    Return the clause identifier for one violation.
    '''
    return str(violation.get("clause", "")).strip()


def collect_failed_identifiers(
        ua1_violations: list,
        wcag_violations: list) -> tuple[set[str], set[str], list[dict]]:
    '''
    Collect failed clause-test IDs plus clause IDs for font-stage matching.
    '''
    identifiers: set[str] = set()
    clause_tests: set[str] = set()
    details: list[dict] = []

    for profile, violations in [("ua1", ua1_violations), ("wcag", wcag_violations)]:
        for violation in violations:
            if not isinstance(violation, dict):
                continue

            clause = get_violation_clause(violation)
            clause_test = get_violation_clause_test(violation)
            if clause_test:
                identifiers.add(clause_test)
                clause_tests.add(clause_test)
            if clause:
                identifiers.add(clause)

            if clause or clause_test:
                details.append({
                    "profile": profile,
                    "clause": clause,
                    "clause_test": clause_test,
                    "description": violation.get("description", ""),
                })

    return identifiers, clause_tests, details


def find_matching_identifiers(
        failed_identifiers: set[str],
        configured_identifiers: list[str]) -> list[str]:
    '''
    Return configured identifiers present in the initial validation failures.
    '''
    return [
        identifier
        for identifier in configured_identifiers
        if identifier in failed_identifiers
    ]


def validation_status(value: object) -> str:
    '''
    Normalize veraPDF result values for JSON summaries.
    '''
    if value is True:
        return "pass"
    if value is False:
        return "fail"
    if value == "Error":
        return "error"
    return str(value)


def validation_counts(validation_result: list) -> dict:
    '''
    Convert a flattened validatePdf result to a compact summary.
    '''
    _, ua1_result, ua1_count, wcag_result, wcag_count, _, _ = validation_result
    return {
        "ua1": {
            "status": validation_status(ua1_result),
            "failed_rules_count": int(ua1_count or 0),
        },
        "wcag": {
            "status": validation_status(wcag_result),
            "failed_rules_count": int(wcag_count or 0),
        },
    }


def validation_passed(validation_result: list, wcag_and_ua1_must_pass: bool) -> bool:
    '''
    Return whether final validation meets the configured pass requirement.
    '''
    _, ua1_result, _, wcag_result, _, _, _ = validation_result
    if wcag_and_ua1_must_pass:
        return ua1_result is True and wcag_result is True
    return wcag_result is True


def validation_had_error(validation_result: list) -> bool:
    '''
    Return whether either validation profile failed operationally.
    '''
    _, ua1_result, _, wcag_result, _, _, _ = validation_result
    return ua1_result == "Error" or wcag_result == "Error"


def write_json_summary(summary_path: Path, summary: dict) -> None:
    '''
    Atomically write solo-summary.json.
    '''
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = summary_path.with_name(f".{summary_path.name}.{uuid4().hex}.tmp")
    tmp_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8"
    )
    tmp_path.replace(summary_path)


def write_workspace_count(report_path: Path, file_count: int = 1) -> None:
    '''
    Write a simple single-file count artifact for solo validation reports.
    '''
    report_path.mkdir(parents=True, exist_ok=True)
    (report_path / "workspace-file-count.csv").write_text(
        f"Total Files\n{file_count}\n",
        encoding="utf-8"
    )


def validate_single_pdf(
        pdf_path: Path,
        report_path: Path,
        workspace_folder_path: Path) -> list:
    '''
    Validate one PDF into an exact report folder.
    '''
    report_path.mkdir(parents=True, exist_ok=True)
    xml_path = report_path / "xml"
    xml_path.mkdir(parents=True, exist_ok=True)

    validation_result = validatePdf(
        str(pdf_path),
        str(xml_path),
        str(workspace_folder_path),
        VALIDATION_PROFILES,
        "xml",
        False
    )

    csv_result = validation_result[:]
    csv_result[0] = get_relative_report_path(
        csv_result[0],
        workspace_folder_path
    )
    del csv_result[5:]
    write_validation_report(report_path, [csv_result])
    write_workspace_count(report_path, 1)

    try:
        run_report_generation(report_path, VALIDATION_PROFILES)
    except SystemExit as exc:
        print_console_message(
            "warn",
            f"Summary report generation skipped for {report_path}: {exc}"
        )

    return validation_result


def create_solo_workspace() -> tuple[Path, Path, Path, Path]:
    '''
    Create a temporary project-shaped workspace for existing helper APIs.
    '''
    scratch_root_path = Path(tempfile.mkdtemp(prefix="pdf-remediation-solo-"))
    workspace_path = scratch_root_path / "workspace"
    files_path = workspace_path / "solo" / "files"
    stages_path = workspace_path / "solo" / "stages"
    files_path.mkdir(parents=True, exist_ok=True)
    stages_path.mkdir(parents=True, exist_ok=True)
    return scratch_root_path, workspace_path, files_path, stages_path


def ensure_stage_output(stage_name: str, output_path: Path) -> None:
    '''
    Fail early if a remediation helper returns without creating the next PDF.
    '''
    if not output_path.is_file():
        raise SoloError(f"{stage_name} did not create an output PDF: {output_path}")


def build_stage_path(stages_path: Path, stage_index: int, input_name: str) -> Path:
    '''
    Build a deterministic scratch path for a stage candidate.
    '''
    input_path = Path(input_name)
    return stages_path / f"{stage_index:02d}-{input_path.stem}{input_path.suffix}"


def run_pdfix_fix_stage(
        current_path: Path,
        next_path: Path,
        config_file: str,
        verbose: bool) -> Path:
    '''
    Run one PDFix action/config stage and return the new candidate path.
    '''
    next_path.parent.mkdir(parents=True, exist_ok=True)
    fix_with_process_timeout(
        str(current_path),
        str(next_path),
        config_file,
        current_path.parent,
        verbose,
        reported_input_pdf_path=str(current_path)
    )
    ensure_stage_output(f"PDFix config {config_file}", next_path)
    return next_path


def run_callas_stage(
        current_path: Path,
        next_path: Path,
        workspace_path: Path,
        verbose: bool) -> Path:
    '''
    Run Callas font remediation and return the new candidate path.
    '''
    pull_image(CALLAS_FONT_IMAGE, verbose=verbose)
    next_path.parent.mkdir(parents=True, exist_ok=True)
    Callas.font_fix(current_path, next_path, workspace_path)
    ensure_stage_output("Callas font fix", next_path)
    current_path.unlink(missing_ok=True)
    return next_path


def run_pdfix_font_stage(
        current_path: Path,
        next_path: Path,
        workspace_path: Path,
        verbose: bool) -> Path:
    '''
    Run Dockerized PDFix missing-unicode remediation and return the candidate.
    '''
    pull_image(PDFIX_FONT_IMAGE, verbose=verbose)
    next_path.parent.mkdir(parents=True, exist_ok=True)
    font_fix_pdfix(current_path, next_path, workspace_path)
    ensure_stage_output("PDFix font fix", next_path)
    return next_path


def replace_output_file(source_path: Path, output_path: Path) -> None:
    '''
    Copy the final candidate into place without exposing a partial output file.
    '''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
    try:
        shutil.copy2(source_path, tmp_path)
        tmp_path.replace(output_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def add_stage(summary: dict, name: str, selected: bool, reason: str = "") -> dict:
    '''
    Append a stage record to the summary.
    '''
    stage = {
        "name": name,
        "selected": selected,
        "status": "pending" if selected else "skipped",
        "reason": reason,
        "started_at": None,
        "finished_at": now_iso() if not selected else None,
    }
    summary["stages"].append(stage)
    return stage


def start_stage(stage: dict) -> None:
    '''
    Mark a stage as running.
    '''
    stage["status"] = "running"
    stage["started_at"] = now_iso()


def finish_stage(stage: dict, status: str = "success", **details) -> None:
    '''
    Mark a stage as finished.
    '''
    stage["status"] = status
    stage["finished_at"] = now_iso()
    stage.update(details)


def fail_stage(stage: dict, exc: Exception) -> None:
    '''
    Mark a stage as failed.
    '''
    finish_stage(stage, "error", error=f"{type(exc).__name__}: {exc}")


def fail_running_stage(summary: dict, exc: Exception) -> None:
    '''
    Mark the current running stage as failed, if one exists.
    '''
    if not summary["stages"]:
        return

    running_stage = summary["stages"][-1]
    if running_stage.get("status") == "running":
        fail_stage(running_stage, exc)


def build_parser() -> argparse.ArgumentParser:
    '''
    Build the solo CLI parser.
    '''
    parser = argparse.ArgumentParser(
        description=(
            "Remediate one PDF without project folder routing. The original "
            "validation controls all optional stages, then the final candidate "
            "is validated once and copied to the requested output path."
        )
    )
    parser.add_argument("input_pdf_path", help="Input PDF path.")
    parser.add_argument("output_pdf_path", help="Output PDF path.")
    parser.add_argument("report_dir", help="Directory where solo reports are written.")
    parser.add_argument(
        "--config-file",
        default="default.json",
        help="PDFix remediation config under resources/configuration (default: %(default)s)."
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        default=DEFAULT_TARGETS,
        help=(
            "Clause-test to action.json mappings. Defaults to the production "
            "targeted metadata, role mapping, and language fixes."
        )
    )
    parser.add_argument(
        "--callas-clause-tests",
        nargs="+",
        default=DEFAULT_CALLAS_CLAUSE_TESTS,
        help="Initial validation identifiers that select the Callas font fix."
    )
    parser.add_argument(
        "--pdfix-font-clause-tests",
        nargs="+",
        default=DEFAULT_PDFIX_FONT_CLAUSE_TESTS,
        help="Initial validation identifiers that select the PDFix font fix."
    )
    parser.add_argument(
        "--wcag-and-ua1-must-pass",
        action="store_true",
        help="Report final pass only when both WCAG and UA1 pass."
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output."
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable debug output."
    )
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Keep the temporary solo workspace for debugging."
    )
    return parser


def print_stage_selection(
        callas_matches: list[str],
        pdfix_font_matches: list[str],
        target_matches: list[str],
        target_actions: list[str]) -> None:
    '''
    Print a compact summary of optional stage selection.
    '''
    print_console_section("INITIAL MATCHES", "info")
    print_console_key_value_rows([
        ("Callas", ", ".join(callas_matches) if callas_matches else "none"),
        (
            "PDFix Font",
            ", ".join(pdfix_font_matches) if pdfix_font_matches else "none"
        ),
        (
            "Target Clauses",
            ", ".join(target_matches) if target_matches else "none"
        ),
        ("Target Actions", ", ".join(target_actions) if target_actions else "none"),
    ])


def run_solo(args: argparse.Namespace, summary: dict, summary_path: Path) -> int:
    '''
    Execute the single-PDF workflow.
    '''
    input_pdf_path = Path(args.input_pdf_path).expanduser().resolve()
    output_pdf_path = Path(args.output_pdf_path).expanduser().resolve()
    report_dir = Path(args.report_dir).expanduser().resolve()

    if args.debug:
        args.verbose = True

    config_path = CONFIG_DIR / args.config_file
    if not config_path.is_file():
        raise SoloError(
            f"Configuration file not found under resources/configuration: {args.config_file}"
        )

    try:
        target_pairs = parse_target_pairs(args.targets)
    except ValueError as exc:
        raise SoloError(str(exc)) from exc

    callas_clause_tests = parse_value_list(args.callas_clause_tests)
    pdfix_font_clause_tests = parse_value_list(args.pdfix_font_clause_tests)

    if not input_pdf_path.is_file():
        raise SoloError(f"Input PDF not found: {input_pdf_path}")
    if input_pdf_path.suffix.lower() != ".pdf":
        raise SoloError(f"Input file must use a .pdf extension: {input_pdf_path}")

    report_dir.mkdir(parents=True, exist_ok=True)
    summary["input_pdf_path"] = str(input_pdf_path)
    summary["output_pdf_path"] = str(output_pdf_path)
    summary["report_dir"] = str(report_dir)

    scratch_root_path, workspace_path, files_path, stages_path = create_solo_workspace()
    summary["workspace"] = {
        "scratch_root_path": str(scratch_root_path),
        "path": str(workspace_path),
        "kept": bool(args.keep_workspace),
    }
    write_json_summary(summary_path, summary)

    try:
        current_path = files_path / input_pdf_path.name
        shutil.copy2(input_pdf_path, current_path)

        print_console_banner("SOLO PDF REMEDIATION", "log")
        print_console_key_value_rows([
            ("Input", input_pdf_path),
            ("Output", output_pdf_path),
            ("Reports", report_dir),
            ("Config File", args.config_file),
            ("WCAG And UA1 Must Pass", args.wcag_and_ua1_must_pass),
            ("Keep Workspace", args.keep_workspace),
            ("Verbose", args.verbose),
            ("Debug", args.debug),
        ])

        initial_stage = add_stage(summary, "initial_validation", True)
        write_json_summary(summary_path, summary)
        start_stage(initial_stage)
        initial_result = validate_single_pdf(
            current_path,
            report_dir / "initial",
            files_path
        )
        initial_counts = validation_counts(initial_result)
        failed_identifiers, failed_clause_tests, violation_details = collect_failed_identifiers(
            initial_result[5],
            initial_result[6]
        )
        finish_stage(
            initial_stage,
            validation=initial_counts,
            report_dir=str(report_dir / "initial")
        )

        callas_matches = find_matching_identifiers(
            failed_identifiers,
            callas_clause_tests
        )
        pdfix_font_matches = find_matching_identifiers(
            failed_identifiers,
            pdfix_font_clause_tests
        )
        target_matches, target_actions = get_matching_actions(
            failed_identifiers,
            target_pairs
        )

        summary["initial_validation"] = {
            "validation": initial_counts,
            "failed_clause_tests": sorted(failed_clause_tests),
            "failed_identifiers": sorted(failed_identifiers),
            "violations": violation_details,
            "matched": {
                "callas_clause_tests": callas_matches,
                "pdfix_font_clause_tests": pdfix_font_matches,
                "target_clause_tests": target_matches,
                "target_actions": target_actions,
            },
        }
        write_json_summary(summary_path, summary)
        print_stage_selection(
            callas_matches,
            pdfix_font_matches,
            target_matches,
            target_actions
        )

        stage_index = 1

        pdfix_stage = add_stage(summary, "pdfix_fix", True)
        write_json_summary(summary_path, summary)
        start_stage(pdfix_stage)
        next_path = build_stage_path(stages_path, stage_index, input_pdf_path.name)
        current_path = run_pdfix_fix_stage(
            current_path,
            next_path,
            args.config_file,
            args.verbose
        )
        finish_stage(
            pdfix_stage,
            config_file=args.config_file,
            output=str(current_path)
        )
        write_json_summary(summary_path, summary)
        stage_index += 1

        callas_stage = add_stage(
            summary,
            "callas_font_fix",
            bool(callas_matches),
            "" if callas_matches
            else "no configured Callas identifiers found in initial validation"
        )
        if callas_matches:
            write_json_summary(summary_path, summary)
            start_stage(callas_stage)
            next_path = build_stage_path(stages_path, stage_index, input_pdf_path.name)
            current_path = run_callas_stage(
                current_path,
                next_path,
                workspace_path,
                args.verbose
            )
            finish_stage(
                callas_stage,
                matched_identifiers=callas_matches,
                output=str(current_path)
            )
            stage_index += 1
        write_json_summary(summary_path, summary)

        pdfix_font_stage = add_stage(
            summary,
            "pdfix_font_fix",
            bool(pdfix_font_matches),
            "" if pdfix_font_matches
            else "no configured PDFix font identifiers found in initial validation"
        )
        if pdfix_font_matches:
            write_json_summary(summary_path, summary)
            start_stage(pdfix_font_stage)
            next_path = build_stage_path(stages_path, stage_index, input_pdf_path.name)
            current_path = run_pdfix_font_stage(
                current_path,
                next_path,
                workspace_path,
                args.verbose
            )
            finish_stage(
                pdfix_font_stage,
                matched_identifiers=pdfix_font_matches,
                output=str(current_path)
            )
            stage_index += 1
        write_json_summary(summary_path, summary)

        target_stage = add_stage(
            summary,
            "fix_target",
            bool(target_actions),
            "" if target_actions
            else "no configured target mappings found in initial validation"
        )
        if target_actions:
            write_json_summary(summary_path, summary)
            start_stage(target_stage)
            for action_name in target_actions:
                next_path = build_stage_path(stages_path, stage_index, input_pdf_path.name)
                current_path = run_pdfix_fix_stage(
                    current_path,
                    next_path,
                    action_name,
                    args.verbose
                )
                stage_index += 1
            finish_stage(
                target_stage,
                matched_clause_tests=target_matches,
                actions=target_actions,
                output=str(current_path)
            )
        write_json_summary(summary_path, summary)

        final_stage = add_stage(summary, "final_validation", True)
        write_json_summary(summary_path, summary)
        start_stage(final_stage)
        final_result = validate_single_pdf(
            current_path,
            report_dir / "final",
            current_path.parent
        )
        final_counts = validation_counts(final_result)
        finish_stage(
            final_stage,
            validation=final_counts,
            report_dir=str(report_dir / "final")
        )

        if validation_had_error(final_result):
            disposition = "validation-error"
        elif validation_passed(final_result, args.wcag_and_ua1_must_pass):
            disposition = "passed"
        else:
            disposition = "failed"

        output_stage = add_stage(summary, "write_output", True)
        write_json_summary(summary_path, summary)
        start_stage(output_stage)
        replace_output_file(current_path, output_pdf_path)
        finish_stage(output_stage, output=str(output_pdf_path))

        summary["final_validation"] = {
            "validation": final_counts,
            "passed": validation_passed(final_result, args.wcag_and_ua1_must_pass),
            "requirement": (
                "wcag_and_ua1"
                if args.wcag_and_ua1_must_pass
                else "wcag"
            ),
        }
        summary["final_disposition"] = disposition
        summary["completed_at"] = now_iso()
        write_json_summary(summary_path, summary)

        print_console_section("SOLO SUMMARY", "success")
        print_console_key_value_rows([
            ("Disposition", disposition),
            ("Output", output_pdf_path),
            ("Summary", summary_path),
        ])

        return 0
    finally:
        if args.keep_workspace:
            print_console_message("info", f"Kept solo workspace: {workspace_path}")
        else:
            shutil.rmtree(scratch_root_path, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    '''
    CLI entrypoint.
    '''
    multiprocessing.freeze_support()
    multiprocessing.set_start_method("spawn", force=True)

    parser = build_parser()
    args = parser.parse_args(argv)

    report_dir = Path(args.report_dir).expanduser().resolve()
    summary_path = report_dir / "solo-summary.json"
    summary = {
        "input_pdf_path": str(Path(args.input_pdf_path).expanduser()),
        "output_pdf_path": str(Path(args.output_pdf_path).expanduser()),
        "report_dir": str(report_dir),
        "config_file": args.config_file,
        "targets": parse_value_list(args.targets),
        "callas_clause_tests": parse_value_list(args.callas_clause_tests),
        "pdfix_font_clause_tests": parse_value_list(args.pdfix_font_clause_tests),
        "wcag_and_ua1_must_pass": bool(args.wcag_and_ua1_must_pass),
        "started_at": now_iso(),
        "completed_at": None,
        "final_disposition": "running",
        "stages": [],
    }

    try:
        report_dir.mkdir(parents=True, exist_ok=True)
        write_json_summary(summary_path, summary)
        return run_solo(args, summary, summary_path)
    except SoloError as exc:
        print_console_message("error", str(exc))
        summary["final_disposition"] = "error"
        summary["completed_at"] = now_iso()
        summary["error"] = str(exc)
        fail_running_stage(summary, exc)
        write_json_summary(summary_path, summary)
        return 1
    except KeyboardInterrupt:
        print_console_message("error", "Interrupted.")
        summary["final_disposition"] = "interrupted"
        summary["completed_at"] = now_iso()
        write_json_summary(summary_path, summary)
        return 130
    except Exception as exc: # pylint: disable=broad-exception-caught
        print_console_message("error", f"{type(exc).__name__}: {exc}")
        summary["final_disposition"] = "error"
        summary["completed_at"] = now_iso()
        summary["error"] = f"{type(exc).__name__}: {exc}"
        fail_running_stage(summary, exc)
        write_json_summary(summary_path, summary)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
