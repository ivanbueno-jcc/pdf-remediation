# pylint: disable=too-many-branches,too-many-arguments,too-many-locals,too-many-positional-arguments, duplicate-code
'''
Validate PDFs in a workspace folder, apply targeted PDFix config files, then
run a final full workspace validation.

When multiple matching clause-tests map to the same action JSON, that action
is only executed once per file.
'''

import argparse
from collections import Counter
from datetime import datetime
import multiprocessing
from pathlib import Path
import shutil
import subprocess
import sys

from parallelbar import progress_starmap

from .fix import fix_with_process_timeout
from .utilities.resources import (
    CONFIG_DIR,
    _route_validated_files,
    clear_workspace_folder,
    get_project_workspace_subfolder_file_paths,
    get_project_workspace_subfolder_path,
    print_console_banner,
    print_console_key_value_rows,
    print_console_message,
    print_console_section,
    print_console_spacer,
    print_workspace_summary,
)
from .utilities.verapdf import validate_pdf_multiprocess


def parse_target_pairs(raw_targets: list[str] | None) -> list[tuple[str, str]]:
    '''
    Parse clause-test:action.json mappings from the CLI.
    '''
    target_pairs: list[tuple[str, str]] = []
    seen_clause_tests: set[str] = set()

    for raw_target in raw_targets or []:
        for target_value in str(raw_target).split(","):
            target_value = target_value.strip()
            if not target_value:
                continue

            if ":" not in target_value:
                raise ValueError(
                    f"Invalid target mapping '{target_value}'. "
                    "Expected clause-test:action.json."
                )

            clause_test, action_name = target_value.split(":", 1)
            clause_test = clause_test.strip()
            action_name = action_name.strip()

            if clause_test == "" or action_name == "":
                raise ValueError(
                    f"Invalid target mapping '{target_value}'. "
                    "Expected clause-test:action.json."
                )

            if clause_test in seen_clause_tests:
                raise ValueError(f"Duplicate clause-test target: {clause_test}")

            config_path = CONFIG_DIR / action_name
            if not config_path.is_file():
                raise ValueError(
                    "Configuration file not found under resources/configuration: "
                    f"{action_name}"
                )

            seen_clause_tests.add(clause_test)
            target_pairs.append((clause_test, action_name))

    if len(target_pairs) == 0:
        raise ValueError(
            "At least one target mapping is required. "
            "Example: --targets 7.1-9:action1.json 5.2-3:action2.json"
        )

    return target_pairs


def get_failed_clause_tests(ua1_violations: list, wcag_violations: list) -> set[str]:
    '''
    Return a unique set of failed clause-test ids for a file.
    '''
    clause_tests = set()
    for violation in ua1_violations + wcag_violations:
        if not isinstance(violation, dict):
            continue

        clause_test = str(violation.get("clause_test", "")).strip()
        if clause_test:
            clause_tests.add(clause_test)
            continue

        clause = str(violation.get("clause", "")).strip()
        if clause == "":
            continue

        test_number = str(
            violation.get("test")
            or violation.get("testNumber")
            or violation.get("testNo")
            or violation.get("testno")
            or ""
        ).strip()
        clause_tests.add(f"{clause}-{test_number}" if test_number else clause)

    return clause_tests


def get_matching_actions(
        failed_clause_tests: set[str],
        target_pairs: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    '''
    Return ordered matching clause-tests and a de-duplicated action sequence.

    If multiple matched clause-tests point to the same action file, the action
    is kept once and executed once for that PDF.
    '''
    matched_clause_tests: list[str] = []
    matched_actions: list[str] = []
    seen_actions: set[str] = set()

    for clause_test, action_name in target_pairs:
        if clause_test not in failed_clause_tests:
            continue

        matched_clause_tests.append(clause_test)
        if action_name not in seen_actions:
            seen_actions.add(action_name)
            matched_actions.append(action_name)

    return matched_clause_tests, matched_actions


def remediate_target_file(
        source_pdf_path: str,
        output_pdf_path: str,
        action_names: tuple[str, ...],
        workspace_folder_path: Path,
        staging_root_path: Path,
        verbose: bool = False) -> dict:
    '''
    Apply one or more PDFix config files to a source PDF and write to processed.
    '''
    source_path = Path(source_pdf_path)
    output_path = Path(output_pdf_path)
    relative_path = source_path.relative_to(workspace_folder_path)
    staged_paths: list[Path] = []
    output_existed = output_path.exists()

    try:
        stage_parent = staging_root_path / relative_path.parent
        stage_parent.mkdir(parents=True, exist_ok=True)

        stage_input_path = stage_parent / f"{source_path.stem}.__fix_target_stage0{source_path.suffix}" # pylint: disable=line-too-long
        shutil.copy2(source_path, stage_input_path)
        staged_paths.append(stage_input_path)

        current_input_path = stage_input_path
        total_actions = len(action_names)
        for index, action_name in enumerate(action_names, start=1):
            is_last_action = index == total_actions
            next_output_path = output_path
            if not is_last_action:
                next_output_path = (
                    stage_parent /
                    f"{source_path.stem}.__fix_target_stage{index}{source_path.suffix}"
                )
                staged_paths.append(next_output_path)

            next_output_path.parent.mkdir(parents=True, exist_ok=True)
            fix_with_process_timeout(
                str(current_input_path),
                str(next_output_path),
                action_name,
                workspace_folder_path,
                verbose
            )
            current_input_path = next_output_path

        source_path.unlink(missing_ok=True)
        return {
            "success": True,
            "source": str(source_path),
            "output": str(output_path),
            "actions": list(action_names),
        }
    except Exception as exc: # pylint: disable=broad-exception-caught
        if not output_existed:
            output_path.unlink(missing_ok=True)

        return {
            "success": False,
            "source": str(source_path),
            "output": str(output_path),
            "actions": list(action_names),
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        for staged_path in staged_paths:
            staged_path.unlink(missing_ok=True)


def cleanup_staging_root(staging_root_path: Path) -> None:
    '''
    Remove any leftover fix_target staging files.
    '''
    if not staging_root_path.exists():
        return

    clear_workspace_folder(staging_root_path)
    try:
        staging_root_path.rmdir()
    except OSError:
        pass

def print_target_pairs(target_pairs: list[tuple[str, str]]) -> None:
    '''
    Print configured clause-test to action mappings.
    '''
    print_console_section("TARGET ACTIONS")
    print_console_key_value_rows(list(target_pairs))


def print_verbose_target_match(
        relative_path: Path,
        matched_clause_tests: list[str],
        matched_actions: list[str]) -> None:
    '''
    Print one file-level target match in a readable block.
    '''
    print_console_message("debug", str(relative_path), indent=2)
    print_console_key_value_rows([
        ("clauses", ", ".join(matched_clause_tests)),
        ("actions", ", ".join(matched_actions)),
    ], indent=6)


def run_module(module: str, module_args: list[str]) -> int:
    '''
    Run a package module with the current Python interpreter.
    '''
    command = [sys.executable, "-m", module, *module_args]
    print_console_message("log", f"RUNNING: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode


def main() -> int: # pylint: disable=too-many-locals,too-many-statements
    '''
    Validate workspace files, apply targeted PDFix actions by clause-test id,
    then run a final full workspace validation.
    '''
    multiprocessing.freeze_support()
    multiprocessing.set_start_method("spawn", force=True)

    parser = argparse.ArgumentParser(
        description=(
            "Validate PDFs in a workspace folder and apply PDFix config files to "
            "documents with matching VERA clause-test violations. If multiple "
            "matched clause-tests use the same action JSON, that action runs "
            "once per file. Finish with validate --full --skip-page-count."
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
        "workspace_folder",
        type=str,
        nargs='?',
        default='active',
        help="Workspace subfolder (default: %(default)s)"
    )
    parser.add_argument(
        "--targets",
        nargs='+',
        required=True,
        help=(
            "Clause-test to action.json mappings. Example: "
            "--targets 7.1-9:action1.json 5.2-3:action2.json. Repeated action "
            "files are de-duplicated per PDF."
        )
    )
    parser.add_argument(
        "--n-cpu",
        type=int,
        default=4,
        help="Number of worker threads to use (default: %(default)s)"
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action='store_true',
        help="Enable verbose output."
    )
    parser.add_argument(
        "--debug",
        "-d",
        action='store_true',
        help="Enable debug output."
    )
    args = parser.parse_args()

    try:
        target_pairs = parse_target_pairs(args.targets)
    except ValueError as exc:
        parser.error(str(exc))

    if args.debug:
        args.verbose = True
        args.n_cpu = 1

    workspace_folder_path = get_project_workspace_subfolder_path(
        args.project_name,
        args.workspace_name,
        args.workspace_folder,
        "files"
    )
    output_pdf_folder = get_project_workspace_subfolder_path(
        args.project_name,
        args.workspace_name,
        args.workspace_folder,
        "processed"
    )
    staging_root_path = workspace_folder_path / ".fix-target-tmp"
    cleanup_staging_root(staging_root_path)

    file_paths = get_project_workspace_subfolder_file_paths(
        args.project_name,
        args.workspace_name,
        args.workspace_folder,
        "files"
    )

    print_console_banner("FIX TARGET")
    print_console_key_value_rows([
        ("Project", args.project_name),
        ("Workspace", args.workspace_name),
        ("Folder", args.workspace_folder),
        ("Source", workspace_folder_path),
        ("Output", output_pdf_folder),
        ("Files Found", len(file_paths)),
        ("Workers", args.n_cpu),
    ])
    print_target_pairs(target_pairs)

    if len(file_paths) == 0:
        print_console_section("NO WORK", "warn")
        print_console_message("warn", "No pending PDF files found.")
        return 0

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    validation_results = validate_pdf_multiprocess(
        workspace_folder_path,
        file_paths,
        timestamp,
        "files"
    )

    remediation_payloads = []
    matched_clause_counter: Counter[str] = Counter()
    multi_action_file_total = 0
    validation_error_total = 0
    printed_match_detail_header = False

    for result in validation_results:
        file_path, ua1_result, _, wcag_result, _, ua1_violations, wcag_violations = result
        if ua1_result == 'Error' or wcag_result == 'Error':
            validation_error_total += 1

        failed_clause_tests = get_failed_clause_tests(ua1_violations, wcag_violations)
        matched_clause_tests, matched_actions = get_matching_actions(
            failed_clause_tests,
            target_pairs
        )

        if len(matched_actions) == 0:
            continue

        if len(matched_actions) > 1:
            multi_action_file_total += 1

        for clause_test in matched_clause_tests:
            matched_clause_counter[clause_test] += 1

        source_path = Path(file_path)
        relative_path = source_path.relative_to(workspace_folder_path)
        destination_path = output_pdf_folder / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        remediation_payloads.append(
            (
                str(source_path),
                str(destination_path),
                tuple(matched_actions),
                workspace_folder_path,
                staging_root_path,
                args.verbose
            )
        )

        if args.verbose:
            if not printed_match_detail_header:
                print_console_section("MATCHED FILE DETAILS", "debug")
                printed_match_detail_header = True
            print_verbose_target_match(relative_path, matched_clause_tests, matched_actions)

    print_console_section("TARGET MATCH SUMMARY", "info")
    print_console_key_value_rows([
        ("Validation Errors", validation_error_total),
        ("Files Selected", len(remediation_payloads)),
        ("Files With Multiple Actions", multi_action_file_total),
    ])
    print_console_spacer()
    print_console_message("log", "Per target:", indent=2)
    print_console_key_value_rows([
        (
            f"{clause_test} -> {action_name}",
            f"{matched_clause_counter.get(clause_test, 0)} files"
        )
        for clause_test, action_name in target_pairs
    ], indent=4)

    remediation_results = []
    if len(remediation_payloads) > 0:
        clear_workspace_folder(staging_root_path)
        print_console_section("REMEDIATING TARGETED FILES", "info")
        remediation_results = progress_starmap(
            remediate_target_file,
            remediation_payloads,
            total=len(remediation_payloads),
            executor="threads",
            n_cpu=args.n_cpu
        )
    else:
        print_console_section("REMEDIATING TARGETED FILES", "warn")
        print_console_message("warn", "No files matched the requested clause-test targets.")

    success_total = sum(1 for result in remediation_results if result.get("success"))
    failed_results = [result for result in remediation_results if not result.get("success")]

    cleanup_staging_root(staging_root_path)

    print_console_section("REMEDIATION SUMMARY", "info")
    print_console_message("success", f"Successful files: {success_total}")
    if len(failed_results) > 0:
        print_console_message("error", f"Failed files: {len(failed_results)}")
    else:
        print_console_message("success", "Failed files: 0")
    for failed_result in failed_results:
        failed_path = Path(failed_result["source"]).relative_to(workspace_folder_path)
        print_console_message("error", f"{failed_path}", indent=2)
        print_console_key_value_rows([("error", failed_result["error"])], indent=6)

    print_console_section("VALIDATING PROCESSED FILES", "info")
    processed_file_paths = get_project_workspace_subfolder_file_paths(
        args.project_name,
        args.workspace_name,
        args.workspace_folder,
        "processed"
    )
    print_console_key_value_rows([("Processed Files Found", len(processed_file_paths))])

    if len(processed_file_paths) > 0:
        processed_validation_results = validate_pdf_multiprocess(
            output_pdf_folder,
            processed_file_paths,
            timestamp,
            "processed"
        )

        print_console_section("ROUTING VALIDATED FILES", "success")
        valid_files_total = _route_validated_files(
            processed_validation_results,
            output_pdf_folder,
            args.project_name,
            args.workspace_name,
            args.verbose
        )
        print_console_message("success", f"Moved to remediated: {valid_files_total}")
    else:
        print_console_message("warn", "No PDF files found for validation.")

    print_console_section("FINAL FULL VALIDATION", "info")
    final_validate_args = [
        args.project_name,
        args.workspace_name,
        "--full",
        "--skip-page-count"
    ]
    rc = run_module("pdf_remediation.validate", final_validate_args)
    if rc != 0:
        print_console_message(
            "error",
            f"Final full validation failed with exit code {rc}."
        )
        return rc

    print_console_section("WORKSPACE SUMMARY", "log")
    print_console_key_value_rows([("Workspace", args.workspace_name)])
    print_workspace_summary(args.project_name, args.workspace_name)

    return 0 if len(failed_results) == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
