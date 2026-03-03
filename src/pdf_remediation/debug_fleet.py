# pylint: disable=duplicate-code
'''
Run debug.py sequentially across multiple projects and aggregate debug files.
'''

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from .utilities.resources import PROJECT_BASE_PATH

DEBUG_FILES_BASE_PATH = Path('resources/debug/_files')


def print_processing_banner(project_name: str, position: int, total: int) -> None:
    '''
    Print a large banner to highlight the project currently being processed.
    '''
    title = f"PROJECT {position}/{total}"
    argument_line = f"{project_name}"
    width = max(120, len(title) + 8, len(argument_line) + 8)
    border = "#" * width

    print()
    print(border)
    print(border)
    print(f"## {title.center(width - 6)} ##")
    print(f"## {argument_line.center(width - 6)} ##")
    print(f"## {' '.center(width - 6)} ##")
    print(border)
    print(border)
    print()


def _collect_project_names(selected_project_names: list[str]) -> tuple[list[str], list[str]]:
    '''
    Build the project name list from selected names or all project directories.
    '''
    if selected_project_names:
        existing: list[str] = []
        missing: list[str] = []
        for project_name in selected_project_names:
            project_path = Path(PROJECT_BASE_PATH) / project_name
            if project_path.is_dir():
                existing.append(project_name)
            else:
                missing.append(project_name)
        return existing, missing

    project_names = sorted([
        project_path.name
        for project_path in Path(PROJECT_BASE_PATH).iterdir()
        if project_path.is_dir()
    ])
    return project_names, []


def run_debug(project_name: str, workspace_name: str, clause_tests: list[str]) -> int:
    '''
    Run the debug module for a single project and return its exit code.
    '''
    command = [
        sys.executable,
        '-m',
        'pdf_remediation.debug',
        project_name,
        workspace_name
    ]
    if clause_tests:
        command.append('--clause-tests')
        command.extend(clause_tests)

    print()
    print(f"RUNNING: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode


def _remove_existing_project_aggregates(project_name: str, debug_files_base_path: Path) -> None:
    '''
    Remove existing aggregated debug output for a project from all clause folders.
    '''
    if not debug_files_base_path.exists():
        return

    for clause_path in debug_files_base_path.iterdir():
        if not clause_path.is_dir():
            continue

        project_path = clause_path / project_name
        if project_path.is_dir():
            shutil.rmtree(project_path)

        if clause_path.is_dir() and not any(clause_path.iterdir()):
            clause_path.rmdir()


def _move_project_debug_files(
        project_name: str,
        workspace_name: str,
        debug_files_base_path: Path) -> tuple[int, int]:
    '''
    Move workspace debug files into resources/debug/_files/<clause-test>/<project>.
    '''
    workspace_debug_path = (
        Path(PROJECT_BASE_PATH)
        / project_name
        / 'workspace'
        / workspace_name
        / 'debug'
    )
    if not workspace_debug_path.exists():
        return 0, 0

    _remove_existing_project_aggregates(project_name, debug_files_base_path)

    moved_clause_folders = 0
    moved_files = 0
    for clause_folder_path in sorted(workspace_debug_path.iterdir(), key=lambda path: path.name):
        if not clause_folder_path.is_dir():
            continue

        destination_project_path = (
            debug_files_base_path
            / clause_folder_path.name
            / project_name
        )
        destination_project_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(clause_folder_path), str(destination_project_path))

        moved_clause_folders += 1
        moved_files += len([path for path in destination_project_path.rglob('*') if path.is_file()])

    return moved_clause_folders, moved_files


def main() -> int:
    '''
    Execute debug.py for selected projects or all projects, then aggregate debug files.
    '''
    parser = argparse.ArgumentParser(
        description=(
            'Run debug.py across all projects in PROJECT_BASE_PATH (or selected '
            'projects), then move debug files into '
            'resources/debug/_files/<clause-test>/<project>.'
        )
    )
    parser.add_argument(
        'project_names',
        nargs='*',
        help=(
            'Optional project names. Omit to run debug for every project '
            'directory in PROJECT_BASE_PATH.'
        )
    )
    parser.add_argument(
        '--workspace-name',
        default='default',
        help='Workspace name to pass to debug.py (default: %(default)s).'
    )
    parser.add_argument(
        '--clause-tests',
        nargs='+',
        default=[],
        help=(
            'Optional clause-test ids to pass through to debug.py. '
            'Example: --clause-tests 6.2.4-1 7.1.3-2'
        )
    )
    args = parser.parse_args()

    project_names, missing_project_names = _collect_project_names(args.project_names)
    if not project_names:
        print(f"No projects found under: {Path(PROJECT_BASE_PATH).resolve()}")
        if missing_project_names:
            print(f"Missing project names: {', '.join(missing_project_names)}")
        return 1

    if missing_project_names:
        print(f"Skipping missing projects: {', '.join(missing_project_names)}")

    debug_files_base_path = DEBUG_FILES_BASE_PATH
    debug_files_base_path.mkdir(parents=True, exist_ok=True)

    print(f"PROJECTS PATH: {Path(PROJECT_BASE_PATH).resolve()}")
    print(f"WORKSPACE: {args.workspace_name}")
    print(f"DEBUG AGGREGATE PATH: {debug_files_base_path.resolve()}")
    if args.clause_tests:
        print(f"CLAUSE-TEST FILTERS: {', '.join(args.clause_tests)}")
    else:
        print('CLAUSE-TEST FILTERS: all')
    print(f"PROJECTS: {len(project_names)}")

    failed_projects: list[tuple[str, int]] = []
    total_moved_clause_folders = 0
    total_moved_files = 0
    total_projects = len(project_names)
    for index, project_name in enumerate(project_names, start=1):
        print_processing_banner(project_name, index, total_projects)
        rc = run_debug(project_name, args.workspace_name, args.clause_tests)
        if rc != 0:
            print()
            print(
                f"debug_fleet warning: debug.py failed for '{project_name}' "
                f"with exit code {rc}."
            )
            failed_projects.append((project_name, rc))
            continue

        moved_clause_folders, moved_files = _move_project_debug_files(
            project_name=project_name,
            workspace_name=args.workspace_name,
            debug_files_base_path=debug_files_base_path
        )
        total_moved_clause_folders += moved_clause_folders
        total_moved_files += moved_files
        print(
            f"MOVED: clause folders={moved_clause_folders}, files={moved_files} "
            f"for project '{project_name}'."
        )

    print()
    print('debug_fleet summary')
    print(f'  Projects attempted: {total_projects}')
    print(f'  Projects failed: {len(failed_projects)}')
    print(f'  Clause folders moved: {total_moved_clause_folders}')
    print(f'  Files moved: {total_moved_files}')

    if failed_projects:
        print('  Failed projects:')
        for project_name, rc in failed_projects:
            print(f'    - {project_name} (exit code {rc})')
        return 1

    print()
    print('debug_fleet completed successfully.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
