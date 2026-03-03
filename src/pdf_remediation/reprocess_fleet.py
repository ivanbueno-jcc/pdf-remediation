# pylint: disable=duplicate-code
'''
Run reprocess.py sequentially across multiple projects.
'''

import argparse
from pathlib import Path
import subprocess
import sys

from .utilities.resources import PROJECT_BASE_PATH


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


def run_reprocess(project_name: str, workspace_name: str, workspace_folder: str) -> int:
    '''
    Run the reprocess module for a single project and return its exit code.
    '''
    command = [
        sys.executable,
        "-m",
        "pdf_remediation.reprocess",
        project_name,
        workspace_name,
        workspace_folder
    ]
    print()
    print(f"RUNNING: {' '.join(command)}")
    result = subprocess.run(command, check=False)
    return result.returncode


def main() -> int:
    '''
    Execute reprocess.py sequentially for selected projects or all projects.
    '''
    parser = argparse.ArgumentParser(
        description=(
            "Run reprocess.py sequentially across all projects in PROJECT_BASE_PATH, "
            "or only selected projects."
        )
    )
    parser.add_argument(
        "project_names",
        nargs="*",
        help=(
            "Optional project names. Omit to run reprocess for every project "
            "directory in PROJECT_BASE_PATH."
        )
    )
    parser.add_argument(
        "--workspace-name",
        default="default",
        help="Workspace name to pass to reprocess.py (default: %(default)s)."
    )
    parser.add_argument(
        "--workspace-folder",
        default="all",
        help="Workspace folder to pass to reprocess.py (default: %(default)s)."
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

    print(f"PROJECTS PATH: {Path(PROJECT_BASE_PATH).resolve()}")
    print(f"WORKSPACE: {args.workspace_name}")
    print(f"FOLDER: {args.workspace_folder}")
    print(f"PROJECTS: {len(project_names)}")

    total_projects = len(project_names)
    for index, project_name in enumerate(project_names, start=1):
        print_processing_banner(project_name, index, total_projects)
        rc = run_reprocess(project_name, args.workspace_name, args.workspace_folder)
        if rc != 0:
            print()
            print(
                f"reprocess_fleet stopped: reprocess.py failed for '{project_name}' "
                f"with exit code {rc}."
            )
            return rc

    print()
    print("reprocess_fleet completed successfully.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
