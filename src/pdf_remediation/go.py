# pylint: disable=duplicate-code,too-many-return-statements,too-many-branches,too-many-statements
'''
Run remediation modules in sequence.
'''

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys

from .utilities.verapdf import validate_pdf_multiprocess
from .utilities.resources import (
    PROJECT_BASE_PATH,
    download_source_with_terminus,
    get_project_workspace_subfolder_file_paths,
    get_project_workspace_subfolder_path,
    get_project_source_path,
    move_file_and_delete_source,
    print_console_banner,
    print_console_key_value_rows,
    print_console_message,
    print_console_section,
)


def run_module(module: str, module_args: list[str]) -> int:
    '''
    Run a package module with the current Python interpreter.
    '''
    command = [sys.executable, "-m", module, *module_args]
    print_console_message("log", f"RUNNING: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode


def print_pipeline_banner(step_number: int, step_name: str) -> None:
    '''
    Print a high-visibility banner for each pipeline step.
    '''
    print_console_banner(f"PIPELINE STEP {step_number}: {step_name}", "info")


def validation_passed_required_compliance(
        ua1_result: bool | str,
        wcag_result: bool | str,
        wcag_and_ua1_must_pass: bool) -> bool:
    '''
    Return whether a validation result satisfies the configured compliance gate.
    '''
    if wcag_and_ua1_must_pass:
        return ua1_result is True and wcag_result is True
    return wcag_result is True


def route_pre_fix_valid_files( # pylint: disable=too-many-arguments,too-many-positional-arguments
        validation_results: list,
        active_files_path: Path,
        project_name: str,
        workspace_name: str,
        wcag_and_ua1_must_pass: bool,
        verbose: bool) -> int:
    '''
    Move pre-fix validation-passing files directly to remediated/files.
    '''
    moved_count = 0
    print_console_section("ROUTING PRE-FIX VALID FILES", "info")
    print_console_key_value_rows([
        (
            "Required Compliance",
            "WCAG + UA1" if wcag_and_ua1_must_pass else "WCAG"
        )
    ])

    for file_path, ua1_result, _, wcag_result, _, _, _ in validation_results:
        if not validation_passed_required_compliance(
            ua1_result,
            wcag_result,
            wcag_and_ua1_must_pass
        ):
            continue

        source_path = Path(file_path)
        if verbose:
            try:
                reported_path = source_path.relative_to(active_files_path)
            except ValueError:
                reported_path = source_path
            print_console_message("debug", f"Pre-fix compliant: {reported_path}", indent=2)

        if move_file_and_delete_source(
            source_path,
            active_files_path,
            project_name,
            workspace_name,
            "remediated"
        ):
            moved_count += 1

    print_console_message(
        "success",
        f"Moved pre-fix compliant files to remediated: {moved_count}"
    )
    return moved_count


def run_required_pre_fix_validation(
        project_name: str,
        workspace_name: str,
        wcag_and_ua1_must_pass: bool,
        verbose: bool) -> int:
    '''
    Validate active/files before remediation and route files that already pass.
    '''
    active_files_path = get_project_workspace_subfolder_path(
        project_name,
        workspace_name,
        "active"
    )
    file_paths = get_project_workspace_subfolder_file_paths(
        project_name,
        workspace_name,
        "active",
        "files"
    )

    print_console_key_value_rows([
        ("Folder", "active"),
        ("Directory", "files"),
        ("PDFs Found", len(file_paths)),
    ])

    if len(file_paths) == 0:
        print_console_section("NO WORK", "warn")
        print_console_message("warn", "No active PDF files found for pre-fix validation.")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    validation_results = validate_pdf_multiprocess(
        active_files_path,
        file_paths,
        timestamp,
        "pre-fix"
    )
    route_pre_fix_valid_files(
        validation_results,
        active_files_path,
        project_name,
        workspace_name,
        wcag_and_ua1_must_pass,
        verbose
    )
    return 0


def main() -> int: # pylint: disable=too-many-locals
    '''
    Run required pre-fix validation, fix, optional font_fix/font_fix_pdfix,
    reprocess all workspace folders, run restore-metadata fix_target actions,
    then final full validation.
    '''
    parser = argparse.ArgumentParser(
        description=(
            "Run required pre-fix validate, fix, optional font_fix/font_fix_pdfix, "
            "reprocess all workspace folders, run restore-metadata fix_target "
            "actions, then validate --full for a project workspace."
        )
    )
    parser.add_argument("project_name", help="Project directory name.")
    parser.add_argument(
        "workspace_name",
        type=str,
        nargs='?',
        default='default',
        help="Workspace name (default: %(default)s)"
    )
    parser.add_argument(
        "--config-file",
        "--c",
        type=str,
        default='default.json',
        help="Configuration file name for fix.py (default: %(default)s)"
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Chunk size for font_fix.py and font_fix_pdfix.py (default: %(default)s)"
    )
    parser.add_argument(
        "--n-cpu",
        type=int,
        default=None,
        help="CPU count for font_fix_pdfix.py (--n-cpu)."
    )
    parser.add_argument(
        "--pre-validate",
        action='store_true',
        help="Deprecated compatibility flag. Pre-fix validation now always runs."
    )
    parser.add_argument(
        "--skip-font-fix",
        action='store_true',
        help="Skip both font_fix and font_fix_pdfix steps."
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action='store_true',
        help="Enable verbose output in each step."
    )
    parser.add_argument(
        "--debug",
        "-d",
        action='store_true',
        help="Enable debug mode in each step."
    )
    parser.add_argument(
        "--wcag-and-ua1-must-pass",
        action='store_true',
        help=(
            "Require pre-fix validation and remediation stages to move files "
            "to remediated only when both WCAG and UA1 pass."
        )
    )
    args = parser.parse_args()

    project_path = Path(PROJECT_BASE_PATH) / args.project_name
    if not project_path.exists():
        print_console_message("warn", f"Project not found. Initializing: {args.project_name}")
        rc = run_module("pdf_remediation.init", [args.project_name])
        if rc != 0:
            print_console_message("error", f"Pipeline stopped: init failed with exit code {rc}.")
            return rc

    source_path = get_project_source_path(args.project_name).resolve()
    source_is_empty = not any(source_path.iterdir())

    if source_is_empty:
        rc = download_source_with_terminus(
            project_name=args.project_name,
            source_path=source_path,
            verbose=args.verbose,
            print_banner=print_pipeline_banner
        )
        if rc != 0:
            return rc

    print_console_banner("GO PIPELINE", "log")
    print_console_key_value_rows([
        ("Project", args.project_name),
        ("Workspace", args.workspace_name),
        ("Source", source_path),
        ("Config File", args.config_file),
        ("Chunk Size", args.chunk_size),
        ("N CPU", args.n_cpu if args.n_cpu is not None else "default"),
        ("Pre Validate", "required"),
        ("Skip Font Fix", args.skip_font_fix),
        ("WCAG And UA1 Must Pass", args.wcag_and_ua1_must_pass),
        ("Verbose", args.verbose),
        ("Debug", args.debug),
    ])
    print_console_section("PIPELINE OVERVIEW", "info")
    print_console_message(
        "",
        "1) validate (--skip-page-count) [pre-fix, required; passing files -> remediated]",
        indent=2
    )
    print_console_message("", "2) fix (active)", indent=2)
    print_console_message(
        "",
        "3) font_fix (font-issues) [optional via --skip-font-fix]",
        indent=2
    )
    print_console_message(
        "",
        "4) font_fix_pdfix (font-issues-missing-unicode) [optional via --skip-font-fix]",
        indent=2
    )
    print_console_message(
        "",
        "5) reprocess (all folders -> active/files)",
        indent=2
    )
    print_console_message(
        "",
        "6) fix_target (active, targets: 5-1 + 7.1-9 -> restore_metadata.json)",
        indent=2
    )
    print_console_message("", "7) validate (--full --skip-page-count) [final]", indent=2)

    fix_args = [
        args.project_name,
        args.workspace_name,
        "active",
        "--config-file",
        args.config_file
    ]
    if args.verbose:
        fix_args.append("--verbose")
    if args.debug:
        fix_args.append("--debug")
    if args.wcag_and_ua1_must_pass:
        fix_args.append("--wcag-and-ua1-must-pass")

    font_fix_args = [
        args.project_name,
        args.workspace_name,
        "font-issues",
        "--chunk-size",
        str(args.chunk_size)
    ]
    if args.wcag_and_ua1_must_pass:
        font_fix_args.append("--wcag-and-ua1-must-pass")
    if args.verbose:
        font_fix_args.append("--verbose")
    if args.debug:
        font_fix_args.append("--debug")

    font_fix_pdfix_args = [
        args.project_name,
        args.workspace_name,
        "font-issues-missing-unicode",
        "--chunk-size",
        str(args.chunk_size)
    ]
    if args.n_cpu is not None:
        font_fix_pdfix_args.extend(["--n-cpu", str(args.n_cpu)])
    if args.wcag_and_ua1_must_pass:
        font_fix_pdfix_args.append("--wcag-and-ua1-must-pass")
    if args.verbose:
        font_fix_pdfix_args.append("--verbose")
    if args.debug:
        font_fix_pdfix_args.append("--debug")

    reprocess_args = [
        args.project_name,
        args.workspace_name,
        "all"
    ]
    fix_target_args = [
        args.project_name,
        args.workspace_name,
        "active",
        "--targets",
        "5-1:restore_metadata.json",
        "7.1-9:restore_metadata.json",
        "7.1-5:role_mapping_fix-7.1-5.json",
        "7.2-29:language_fix-7.2-29.json",
        "--skip-final-full-validation"
    ]
    if args.wcag_and_ua1_must_pass:
        fix_target_args.append("--wcag-and-ua1-must-pass")
    if args.verbose:
        fix_target_args.append("--verbose")
    if args.debug:
        fix_target_args.append("--debug")

    final_validate_args = [
        args.project_name,
        args.workspace_name,
        "--skip-page-count",
        "--full"
    ]

    print_pipeline_banner(1, "validate (pre-fix)")
    rc = run_required_pre_fix_validation(
        args.project_name,
        args.workspace_name,
        args.wcag_and_ua1_must_pass,
        args.verbose
    )
    if rc != 0:
        print_console_message(
            "error",
            f"Pipeline stopped: pre-fix validate failed with exit code {rc}."
        )
        return rc

    print_pipeline_banner(2, "fix")
    rc = run_module("pdf_remediation.fix", fix_args)
    if rc != 0:
        print_console_message("error", f"Pipeline stopped: fix failed with exit code {rc}.")
        return rc

    if args.skip_font_fix:
        print_console_message(
            "warn",
            "Skipping font_fix and font_fix_pdfix (pass without --skip-font-fix to enable)."
        )
    else:
        print_pipeline_banner(3, "font_fix")
        rc = run_module("pdf_remediation.font_fix", font_fix_args)
        if rc != 0:
            print_console_message(
                "error",
                f"Pipeline stopped: font_fix failed with exit code {rc}."
            )
            return rc

        print_pipeline_banner(4, "font_fix_pdfix")
        rc = run_module("pdf_remediation.font_fix_pdfix", font_fix_pdfix_args)
        if rc != 0:
            print_console_message(
                "error",
                f"Pipeline stopped: font_fix_pdfix failed with exit code {rc}."
            )
            return rc

    print_pipeline_banner(5, "reprocess")
    rc = run_module("pdf_remediation.reprocess", reprocess_args)
    if rc != 0:
        print_console_message(
            "error",
            f"Pipeline stopped: reprocess failed with exit code {rc}."
        )
        return rc

    print_pipeline_banner(6, "fix_target")
    rc = run_module("pdf_remediation.fix_target", fix_target_args)
    if rc != 0:
        print_console_message(
            "error",
            f"Pipeline stopped: fix_target failed with exit code {rc}."
        )
        return rc

    print_pipeline_banner(7, "validate (final)")
    rc = run_module("pdf_remediation.validate", final_validate_args)
    if rc != 0:
        print_console_message(
            "error",
            f"Pipeline stopped: final validate failed with exit code {rc}."
        )
        return rc

    print_console_message("success", "Pipeline completed successfully.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
