'''
Validate PDF files in a project workspace.
'''
import argparse
from datetime import datetime
import sys
from .utilities.pdfix import get_page_count_multiprocess
from .utilities.verapdf import validate_pdf_multiprocess
from .utilities.resources import get_project_source_path
from .utilities.resources import get_project_workspace_subfolder_file_paths
from .utilities.resources import get_project_workspace_subfolder_path

if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Validate PDF files in a project workspace."
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
        "directory",
        type=str,
        nargs='?',
        default='files',
        help="Workspace subfolder directory (default: %(default)s)"
    )
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print(f"WORKSPACE: {args.workspace_name}")
        print(f"FOLDER: {args.workspace_folder}")
        print(f"DIRECTORY: {args.directory}")
        print()

        source_path = get_project_source_path(args.project_name)
        workspace_folder_path = get_project_workspace_subfolder_path(
            args.project_name,
            args.workspace_name,
            args.workspace_folder,
            args.directory)
        file_paths = get_project_workspace_subfolder_file_paths(
            args.project_name,
            args.workspace_name,
            args.workspace_folder,
            args.directory
        )

        if len(file_paths) > 0:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            total_pages = get_page_count_multiprocess(
                workspace_folder_path,
                file_paths, timestamp,
                args.directory
            )
            validate_pdf_multiprocess(workspace_folder_path, file_paths, timestamp, args.directory)
        else:
            print("No PDF files found.")
            sys.exit()
