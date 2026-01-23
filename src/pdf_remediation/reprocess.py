'''
Move processed PDF files back to the active workspace folder.
'''

import argparse
from pathlib import Path
import sys
from .utilities.resources import clear_workspace_folder, get_project_workspace_subfolder_path
from .utilities.resources import move_file_and_delete_source

if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Move the processed files back to the active workspace folder."
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
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print(f"WORKSPACE: {args.workspace_name}")
        print(f"FOLDER: {args.workspace_folder}")
        print()

        workspace_folder_path = get_project_workspace_subfolder_path(
            args.project_name,
            args.workspace_name,
            args.workspace_folder
        )
        clear_workspace_folder(workspace_folder_path)
        semaphore = workspace_folder_path / ".remediation.lock"
        semaphore.touch(exist_ok=True)

        workspace_folder_path_processed = get_project_workspace_subfolder_path(
            args.project_name,
            args.workspace_name,
            args.workspace_folder,
            "processed"
        )

        if len(list(workspace_folder_path_processed.rglob("*.pdf"))) > 0:
            for file_path in workspace_folder_path_processed.rglob("*.pdf"):
                move_file_and_delete_source(
                    Path(file_path),
                    workspace_folder_path_processed,
                    args.project_name,
                    args.workspace_name,
                    args.workspace_folder
                )
            print("Processed files have been moved back to the active folder.")
        else:
            print("No PDF files found in the processed folder.")
            sys.exit()
