'''
Reset the files in the working directory with the source files.
'''

import argparse
import sys
from .utilities.Resources import get_project_source_path
from .utilities.Resources import get_project_workspace_subfolder_path, clear_workspace_folder

if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Reset the files in the working directory with the source files."
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

        source_path = get_project_source_path(args.project_name)
        workspace_folder_path = get_project_workspace_subfolder_path(
            args.project_name,
            args.workspace_name,
            args.workspace_folder
        )
        clear_workspace_folder(workspace_folder_path)

        workspace_folder_path_processed = get_project_workspace_subfolder_path(
            args.project_name,
            args.workspace_name,
            args.workspace_folder,
            "processed"
        )
        clear_workspace_folder(workspace_folder_path_processed)

        semaphore = workspace_folder_path / ".remediation.lock"
        if len(list(source_path.rglob("*.pdf"))) > 0:
            for file_path in source_path.rglob("*.pdf"):
                relative_path = file_path.relative_to(source_path)
                destination_path = workspace_folder_path / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(file_path.read_bytes())

            # Add a semaphore to only copy over the source once, until reset.
            semaphore.touch(exist_ok=True)

            print("Files are overwritten with originals.")
        else:
            print("No PDF files found in the source.")
            sys.exit()
