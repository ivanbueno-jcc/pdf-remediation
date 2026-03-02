# pylint: disable=duplicate-code
'''
Move processed PDF files back to the active workspace folder.
'''

import argparse
from pathlib import Path
import sys
from .utilities.resources import get_pdf_file_paths, get_project_workspace_path
from .utilities.resources import move_file_and_delete_source

IGNORED_WORKSPACE_FOLDERS = {
    "remediated",
    "pdfix-cannot-process",
    "secured-cannot-process",
    "secured-needs-approval",
    "reports",
    "pdfix-unable-to-open",
    "unable-to-validate",
    "unable-to-process",
    "debug"
}

if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Move processed files from workspace folders back to active/files."
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
        default='all',
        help="Workspace subfolder to scan (default: %(default)s)"
    )
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print(f"WORKSPACE: {args.workspace_name}")
        print(f"FOLDER: {args.workspace_folder}")
        print()

        workspace_path = get_project_workspace_path(
            args.project_name,
            args.workspace_name
        )

        workspace_folders = []
        if args.workspace_folder == "all":
            workspace_folders = sorted([
                subfolder_path.name
                for subfolder_path in workspace_path.iterdir()
                if (
                    subfolder_path.is_dir()
                    and subfolder_path.name not in IGNORED_WORKSPACE_FOLDERS
                )
            ])
        else:
            workspace_folders = [args.workspace_folder]

        workspace_folders = [
            workspace_folder
            for workspace_folder in workspace_folders
            if workspace_folder not in IGNORED_WORKSPACE_FOLDERS
        ]

        total_files_moved = 0
        source_directories = ["processed", "files"]
        for workspace_folder in workspace_folders:
            for source_directory in source_directories:
                if workspace_folder == "active" and source_directory == "files":
                    continue

                source_folder_path = workspace_path / workspace_folder / source_directory
                if not source_folder_path.exists():
                    continue

                source_file_paths = get_pdf_file_paths(source_folder_path)
                if len(source_file_paths) == 0:
                    continue

                for file_path in source_file_paths:
                    move_file_and_delete_source(
                        Path(file_path),
                        source_folder_path,
                        args.project_name,
                        args.workspace_name,
                        "active"
                    )

                total_files_moved += len(source_file_paths)
                print(
                    f"Moved {len(source_file_paths)} files from "
                    f"{workspace_folder}/{source_directory} to active/files."
                )

        if total_files_moved > 0:
            print()
            print(f"Total files moved to active/files: {total_files_moved}")
        else:
            print("No PDF files found in processed or files folders.")
            sys.exit()
