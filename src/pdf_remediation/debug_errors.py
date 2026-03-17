# pylint: disable=too-many-locals
'''
Copy files from pdfix-cannot-process-files.csv into error-type debug folders.
'''

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import shutil

from .utilities.resources import (
    PROJECT_BASE_PATH,
    print_console_banner,
    print_console_key_value_rows,
    print_console_message,
    print_console_section,
)

ERROR_CODE_PATTERN = re.compile(r'\bcode\s*(-?\d+)\b', re.IGNORECASE)
MAX_ERROR_FOLDER_NAME_LENGTH = 80
WORKSPACE_SUBFOLDER_PRIORITY = {
    'active': 0,
    'font-issues': 1,
    'font-issues-missing-unicode': 2,
    'remediated': 3,
}


@dataclass
class ProjectSummary:
    '''
    Track copy results for one project.
    '''
    project_name: str
    rows_scanned: int = 0
    error_rows: int = 0
    copied: int = 0
    unresolved: int = 0
    renamed_for_collision: int = 0
    skipped_reason: str = ''


def _normalize_csv_path(csv_value: str) -> str:
    '''
    Normalize CSV path values for matching.
    '''
    normalized = (csv_value or '').strip().replace('\\', '/')
    normalized = normalized.lstrip('/')
    while '//' in normalized:
        normalized = normalized.replace('//', '/')
    return normalized


def _sanitize_error_folder_name(error_message: str) -> str:
    '''
    Convert raw error message text into a filesystem-safe folder name.
    '''
    normalized = re.sub(r'\s+', ' ', (error_message or '').strip().lower())
    if not normalized:
        return 'unknown-error'

    safe_chars = []
    for char in normalized:
        if char.isalnum() or char in {' ', '.', '-', '_'}:
            safe_chars.append(char)
        else:
            safe_chars.append('_')

    folder_name = ''.join(safe_chars).strip(' ._')
    folder_name = re.sub(r'\s+', ' ', folder_name)
    if not folder_name:
        return 'unknown-error'

    if len(folder_name) > MAX_ERROR_FOLDER_NAME_LENGTH:
        folder_name = folder_name[:MAX_ERROR_FOLDER_NAME_LENGTH].rstrip(' ._-')

    return folder_name or 'unknown-error'


def _get_error_type_folder(error_message: str) -> str | None:
    '''
    Map a CSV error message to the destination error-type folder name.
    '''
    normalized = re.sub(r'\s+', ' ', (error_message or '').strip())
    if not normalized:
        return None

    code_match = ERROR_CODE_PATTERN.search(normalized)
    if code_match:
        return f'code {code_match.group(1)}'

    if 'timeouterror' in normalized.casefold():
        return 'timeout'

    return _sanitize_error_folder_name(normalized)


def _build_flat_destination_path(
        destination_folder: Path,
        source_file_name: str,
        reserved_paths: set[Path]) -> tuple[Path, bool]:
    '''
    Return a destination path in a flat folder, adding numeric suffixes on collision.
    '''
    source_name_path = Path(source_file_name)
    candidate_name = source_name_path.name or 'unknown.pdf'
    stem = Path(candidate_name).stem
    suffix = Path(candidate_name).suffix

    destination_path = destination_folder / candidate_name
    had_collision = False
    counter = 2
    while destination_path.exists() or destination_path in reserved_paths:
        had_collision = True
        destination_path = destination_folder / f'{stem}_{counter}{suffix}'
        counter += 1

    reserved_paths.add(destination_path)
    return destination_path, had_collision


def _build_workspace_pdf_index(workspace_path: Path) -> dict[str, list[Path]]:
    '''
    Build a {filename: [full_path...]} index for all PDFs under workspace.
    '''
    index: dict[str, list[Path]] = {}
    if not workspace_path.exists():
        return index

    for file_path in sorted(workspace_path.rglob('*')):
        if not file_path.is_file() or file_path.suffix.lower() != '.pdf':
            continue
        index.setdefault(file_path.name, []).append(file_path)

    return index


def _workspace_candidate_sort_key(workspace_path: Path, candidate_path: Path) -> tuple:
    '''
    Prefer files in active/files first, then other known workspace subfolders.
    '''
    relative_path = candidate_path.relative_to(workspace_path)
    path_parts = relative_path.parts
    top_level_folder = path_parts[0] if path_parts else ''
    second_level_folder = path_parts[1] if len(path_parts) > 1 else ''

    top_level_priority = WORKSPACE_SUBFOLDER_PRIORITY.get(top_level_folder, 99)
    second_level_priority = 0 if second_level_folder == 'files' else 1

    return (
        top_level_priority,
        second_level_priority,
        len(path_parts),
        relative_path.as_posix()
    )


def _select_workspace_file(
        workspace_path: Path,
        index: dict[str, list[Path]],
        csv_relative_path: str) -> Path | None:
    '''
    Resolve a CSV path to the best matching file under workspace/default.
    '''
    file_name = PurePosixPath(csv_relative_path).name
    if not file_name:
        return None

    candidates = index.get(file_name, [])
    if not candidates:
        return None

    normalized_target = csv_relative_path.casefold()
    suffix_matches = []
    for candidate_path in candidates:
        relative_text = candidate_path.relative_to(workspace_path).as_posix()
        relative_casefold = relative_text.casefold()
        if (
            relative_casefold == normalized_target
            or relative_casefold.endswith(f'/{normalized_target}')
        ):
            suffix_matches.append(candidate_path)

    if suffix_matches:
        candidates = suffix_matches

    return sorted(
        candidates,
        key=lambda path: _workspace_candidate_sort_key(workspace_path, path)
    )[0]


def _process_project(
        project_path: Path,
        workspace_name: str,
        debug_base_path: Path,
        dry_run: bool = False,
        verbose: bool = False) -> ProjectSummary:
    '''
    Process one project CSV and copy matching files to the debug destination.
    '''
    summary = ProjectSummary(project_name=project_path.name)
    csv_path = project_path / 'pdfix-cannot-process-files.csv'
    workspace_path = project_path / 'workspace' / workspace_name

    if not csv_path.exists():
        summary.skipped_reason = f'missing {csv_path.name}'
        return summary

    workspace_pdf_index = _build_workspace_pdf_index(workspace_path)
    reserved_destination_paths: dict[Path, set[Path]] = {}

    with open(csv_path, newline='', encoding='utf-8', errors='ignore') as csv_file:
        reader = csv.reader(csv_file)
        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue

            summary.rows_scanned += 1
            source_relative_path = row[0].strip() if row else ''
            error_message = ','.join(row[1:]).strip() if len(row) > 1 else ''

            error_type_folder = _get_error_type_folder(error_message)
            if error_type_folder is None:
                continue

            summary.error_rows += 1
            normalized_csv_path = _normalize_csv_path(source_relative_path)
            if not normalized_csv_path:
                summary.unresolved += 1
                continue

            source_file_path = _select_workspace_file(
                workspace_path=workspace_path,
                index=workspace_pdf_index,
                csv_relative_path=normalized_csv_path
            )
            if source_file_path is None:
                summary.unresolved += 1
                if verbose:
                    print_console_message(
                        "warn",
                        f"[MISS] {project_path.name}: {normalized_csv_path}",
                        indent=2
                    )
                continue

            project_debug_path = debug_base_path / error_type_folder / project_path.name
            if not dry_run:
                project_debug_path.mkdir(parents=True, exist_ok=True)

            if project_debug_path not in reserved_destination_paths:
                reserved_destination_paths[project_debug_path] = set()

            destination_path, had_collision = _build_flat_destination_path(
                destination_folder=project_debug_path,
                source_file_name=source_file_path.name,
                reserved_paths=reserved_destination_paths[project_debug_path]
            )
            if had_collision:
                summary.renamed_for_collision += 1

            if not dry_run:
                shutil.copy2(source_file_path, destination_path)

            summary.copied += 1
            if verbose:
                print_console_message(
                    "debug",
                    f"[COPY] {project_path.name} [{error_type_folder}]: "
                    f"{source_file_path} -> {destination_path}"
                )

    return summary


def _collect_project_paths(
        projects_path: Path,
        selected_project_names: list[str]) -> list[Path]:
    '''
    Return project paths to process.
    '''
    if selected_project_names:
        paths = []
        for project_name in selected_project_names:
            project_path = projects_path / project_name
            if project_path.is_dir():
                paths.append(project_path)
        return sorted(paths, key=lambda path: path.name)

    return sorted(
        [path for path in projects_path.iterdir() if path.is_dir()],
        key=lambda path: path.name
    )


def main() -> int:
    '''
    Parse all project CSVs and copy failed files into error-type debug folders.
    '''
    parser = argparse.ArgumentParser(
        description=(
            'Parse resources/projects/*/pdfix-cannot-process-files.csv, '
            'parse each error type, then copy matched files from workspace/default '
            'into resources/debug/<error-type>/<project>.'
        )
    )
    parser.add_argument(
        'project_names',
        nargs='*',
        help='Optional project names. Omit to process every project in resources/projects.'
    )
    parser.add_argument(
        '--workspace-name',
        default='default',
        help='Workspace name to scan (default: %(default)s).'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be copied without writing files.'
    )
    parser.add_argument(
        '--verbose',
        '-v',
        action='store_true',
        help='Print copied and unresolved file details.'
    )
    args = parser.parse_args()

    projects_path = PROJECT_BASE_PATH
    debug_base_path = Path('resources/debug')
    project_paths = _collect_project_paths(projects_path, args.project_names)
    if not project_paths:
        print_console_section("NO PROJECTS", "warn")
        print_console_message("warn", f"No projects found under: {projects_path.resolve()}")
        return 1

    print_console_banner("DEBUG ERRORS")
    print_console_key_value_rows([
        ("Projects Path", projects_path.resolve()),
        ("Debug Path", debug_base_path.resolve()),
        ("Workspace", args.workspace_name),
        ("Dry Run", args.dry_run),
        ("Projects", len(project_paths)),
    ])

    total_rows = 0
    total_error_rows = 0
    total_copied = 0
    total_unresolved = 0
    total_renamed_for_collision = 0

    for project_path in project_paths:
        summary = _process_project(
            project_path=project_path,
            workspace_name=args.workspace_name,
            debug_base_path=debug_base_path,
            dry_run=args.dry_run,
            verbose=args.verbose
        )

        if summary.skipped_reason:
            print_console_message(
                "warn",
                f"{summary.project_name}: skipped ({summary.skipped_reason})"
            )
            continue

        print_console_message(
            "log",
            f'{summary.project_name}: '
            f'rows={summary.rows_scanned}, '
            f'errors={summary.error_rows}, '
            f'copied={summary.copied}, '
            f'unresolved={summary.unresolved}, '
            f'renamed={summary.renamed_for_collision}'
        )

        total_rows += summary.rows_scanned
        total_error_rows += summary.error_rows
        total_copied += summary.copied
        total_unresolved += summary.unresolved
        total_renamed_for_collision += summary.renamed_for_collision

    print_console_section("TOTALS", "info")
    print_console_key_value_rows([
        ("Rows Scanned", total_rows),
        ("Error Rows", total_error_rows),
        ("Copied", total_copied),
        ("Unresolved", total_unresolved),
        ("Renamed Due To Collisions", total_renamed_for_collision),
    ])

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
