# pylint: disable=duplicate-code
'''
Download latest source files (when Terminus is available) and copy only new files
into active/files for a workspace.
'''

import argparse
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

from .utilities.resources import (
    clear_workspace_folder,
    download_source_with_terminus_result,
    get_pdf_file_paths,
    get_project_source_path,
    get_project_workspace_path,
    get_project_workspace_subfolder_path,
    print_console_banner,
    print_console_key_value_rows,
    print_console_message,
    print_console_section,
)

IGNORED_WORKSPACE_FOLDERS = {"debug", "reports"}
TRACKED_WORKSPACE_DIRECTORIES = ("files", "processed")


def _replace_directory_contents(destination_path: Path, staged_path: Path) -> None:
    '''
    Replace destination_path contents with the staged contents.
    '''
    clear_workspace_folder(destination_path)

    for entry in sorted(staged_path.iterdir()):
        shutil.move(str(entry), str(destination_path / entry.name))


def _get_existing_workspace_relative_paths(workspace_path: Path) -> set[str]:
    '''
    Return all PDF paths in workspace files/ and processed/ as relative strings.
    '''
    existing_paths = set()

    for workspace_subfolder_path in sorted(workspace_path.iterdir()):
        if not workspace_subfolder_path.is_dir():
            continue
        if workspace_subfolder_path.name in IGNORED_WORKSPACE_FOLDERS:
            continue

        for directory_name in TRACKED_WORKSPACE_DIRECTORIES:
            directory_path = workspace_subfolder_path / directory_name
            if not directory_path.exists():
                continue

            for file_path in sorted(get_pdf_file_paths(directory_path)):
                relative_path = file_path.relative_to(directory_path).as_posix()
                existing_paths.add(relative_path)

    return existing_paths

# pylint: disable=too-many-locals
def get_latest_files(project_name: str, workspace: str = "default") -> int:
    '''
    Refresh source from Terminus when available, then copy only new files into
    workspace/active/files.

    Returns 0 on success and non-zero on fatal Terminus failure.
    '''
    source_path = get_project_source_path(project_name)
    workspace_path = get_project_workspace_path(project_name, workspace)

    terminus_path = shutil.which("terminus")
    if terminus_path is not None:
        print_console_message("info", f"Terminus detected: {terminus_path}")
        print_console_message("info", f"Refreshing source folder: {source_path.resolve()}")

        with TemporaryDirectory(
            prefix=f"{project_name}-source-",
            dir=source_path.parent
        ) as staged_source_root:
            staged_source_path = Path(staged_source_root)
            rc, downloaded = download_source_with_terminus_result(
                project_name=project_name,
                source_path=staged_source_path
            )
            if rc != 0:
                return rc
            if downloaded:
                _replace_directory_contents(source_path, staged_source_path)
            else:
                print_console_message(
                    "warn",
                    "Terminus download was skipped. Keeping the existing source folder."
                )
    else:
        print_console_message("warn", "Terminus not detected. Skipping source download.")

    source_file_paths = sorted(get_pdf_file_paths(source_path))
    if len(source_file_paths) == 0:
        print_console_section("NO SOURCE FILES", "warn")
        print_console_message("warn", "No PDF files found in source.")
        return 0

    existing_workspace_relative_paths = _get_existing_workspace_relative_paths(
        workspace_path
    )
    active_files_path = get_project_workspace_subfolder_path(
        project_name,
        workspace,
        "active",
        "files"
    )

    copied_count = 0
    for source_file_path in source_file_paths:
        relative_path = source_file_path.relative_to(source_path)
        relative_path_key = relative_path.as_posix()
        if relative_path_key in existing_workspace_relative_paths:
            continue

        destination_path = active_files_path / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file_path, destination_path)
        copied_count += 1
        print_console_message("success", f"Copied: {relative_path_key}", indent=2)

    print_console_section("SYNC SUMMARY", "success")
    print_console_key_value_rows([
        ("Source Files Scanned", len(source_file_paths)),
        ("New Files Copied", copied_count),
        ("Destination", active_files_path.resolve()),
    ])
    return 0


def main() -> int:
    '''
    Parse CLI args and run get_latest_files.
    '''
    parser = argparse.ArgumentParser(
        description=(
            "Refresh source via Terminus when available and copy only new source "
            "files into workspace/active/files."
        )
    )
    parser.add_argument("project_name", help="Project directory name.")
    parser.add_argument(
        "workspace",
        type=str,
        nargs='?',
        default='default',
        help="Workspace name (default: %(default)s)"
    )
    args = parser.parse_args()

    print_console_banner("GET LATEST FILES")
    print_console_key_value_rows([
        ("Project", args.project_name),
        ("Workspace", args.workspace),
    ])

    return get_latest_files(args.project_name, args.workspace)


if __name__ == '__main__':
    raise SystemExit(main())
