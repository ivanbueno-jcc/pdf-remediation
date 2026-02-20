# pylint: disable=duplicate-code, too-many-locals
'''
Validate active files and copy failures into clause-based debug folders.
'''

import argparse
from datetime import datetime
from pathlib import Path
import shutil

from .utilities.resources import (
    clear_workspace_folder,
    get_project_workspace_path,
    get_project_workspace_subfolder_file_paths,
    get_project_workspace_subfolder_path,
)
from .utilities.verapdf import validate_pdf_multiprocess


def sanitize_clause_folder_name(clause: str) -> str:
    '''
    Convert a clause label into a filesystem-safe folder name.
    '''
    clause = (clause or "").strip()
    if clause == "":
        return "unknown"

    safe_chars = []
    for char in clause:
        if char.isalnum() or char in {".", "-", "_"}:
            safe_chars.append(char)
        else:
            safe_chars.append("_")

    folder_name = "".join(safe_chars).strip("._")
    return folder_name if folder_name else "unknown"


def get_failed_clause_test_ids(ua1_violations: list, wcag_violations: list) -> set[str]:
    '''
    Return a unique set of clause-test ids found in validation violations.
    '''
    clause_test_ids = set()
    for violation in ua1_violations + wcag_violations:
        if not isinstance(violation, dict):
            continue
        clause = str(violation.get("clause", "")).strip()
        if clause:
            test_number = str(
                violation.get("test")
                or violation.get("testNumber")
                or violation.get("testNo")
                or violation.get("testno")
                or ""
            ).strip()
            clause_test_id = f"{clause}-{test_number}" if test_number else clause
            clause_test_ids.add(clause_test_id)
    return clause_test_ids


def copy_file_to_debug_clause_folders(
        source_file_path: Path,
        debug_root_path: Path,
        clause_test_ids: set[str]) -> int:
    '''
    Copy a PDF into one debug folder per clause-test id using a flat filename layout.
    '''
    copy_total = 0
    for clause_test_id in clause_test_ids:
        clause_folder = sanitize_clause_folder_name(clause_test_id)
        clause_path = debug_root_path / clause_folder
        clause_path.mkdir(parents=True, exist_ok=True)

        destination_path = clause_path / source_file_path.name
        if destination_path.exists():
            counter = 2
            while True:
                destination_path = (
                    clause_path /
                    f"{source_file_path.stem}_{counter}{source_file_path.suffix}"
                )
                if not destination_path.exists():
                    break
                counter += 1

        shutil.copy2(source_file_path, destination_path)
        copy_total += 1

    return copy_total


def main() -> int:
    '''
    Run validation on active/files and copy failures into debug/<clause-test>/ folders.
    '''
    parser = argparse.ArgumentParser(
        description=(
            "Validate PDFs in active/files and copy non-compliant files into "
            "workspace/<workspace>/debug/<clause-test>/ folders."
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
    args = parser.parse_args()

    print(f"PROJECT: {args.project_name}")
    print(f"WORKSPACE: {args.workspace_name}")
    print()

    active_files_path = get_project_workspace_subfolder_path(
        args.project_name,
        args.workspace_name,
        "active",
        "files"
    )
    file_paths = get_project_workspace_subfolder_file_paths(
        args.project_name,
        args.workspace_name,
        "active",
        "files"
    )

    print(f"SOURCE: {active_files_path}")
    print(f"FILES FOUND: {len(file_paths)}")
    print()

    if len(file_paths) == 0:
        print("No pending PDF files found.")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    validation_results = validate_pdf_multiprocess(
        active_files_path,
        file_paths,
        timestamp,
        "files"
    )

    workspace_path = get_project_workspace_path(args.project_name, args.workspace_name)
    debug_root_path = workspace_path / "debug"
    clear_workspace_folder(debug_root_path)

    print("COPYING NON-COMPLIANT FILES INTO DEBUG CLAUSE FOLDERS...")
    print(f"DEBUG TARGET: {debug_root_path}")
    print()

    failed_files_total = 0
    copied_files_total = 0
    unknown_clause_total = 0

    for result in validation_results:
        file_path, ua1_result, _, wcag_result, _, ua1_violations, wcag_violations = result

        if ua1_result is True and wcag_result is True:
            continue

        failed_files_total += 1
        failed_clause_tests = get_failed_clause_test_ids(ua1_violations, wcag_violations)
        if len(failed_clause_tests) == 0:
            unknown_clause_total += 1
            failed_clause_tests = {"unknown"}

        copied_files_total += copy_file_to_debug_clause_folders(
            Path(file_path),
            debug_root_path,
            failed_clause_tests
        )

    print("DEBUG SUMMARY")
    print(f"  Failed files: {failed_files_total}")
    print(f"  Files copied (including multi-clause copies): {copied_files_total}")
    print(f"  Files routed to unknown clause: {unknown_clause_total}")
    print(f"  Clause folders: {len([p for p in debug_root_path.iterdir() if p.is_dir()])}")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
