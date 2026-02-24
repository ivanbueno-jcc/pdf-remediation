# pylint: disable=duplicate-code
'''
Move processed PDF files back to the active workspace folder.
'''

import argparse
from pathlib import Path
import sys
from .utilities.resources import get_pdf_file_paths, get_project_workspace_path
from .utilities.resources import move_file_and_delete_source

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
                if subfolder_path.is_dir()
            ])
        else:
            workspace_folders = [args.workspace_folder]

        total_files_moved = 0
        for workspace_folder in workspace_folders:
            processed_folder_path = workspace_path / workspace_folder / "processed"
            if not processed_folder_path.exists():
                continue

            processed_file_paths = get_pdf_file_paths(processed_folder_path)
            if len(processed_file_paths) == 0:
                continue

            for file_path in processed_file_paths:
                move_file_and_delete_source(
                    Path(file_path),
                    processed_folder_path,
                    args.project_name,
                    args.workspace_name,
                    "active"
                )

            total_files_moved += len(processed_file_paths)
            print(
                f"Moved {len(processed_file_paths)} files from "
                f"{workspace_folder}/processed to active/files."
            )

        if total_files_moved > 0:
            print()
            print(f"Total files moved to active/files: {total_files_moved}")
        else:
            print("No PDF files found in processed folders.")
            sys.exit()
