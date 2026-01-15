from .utilities.PDFix import get_page_count_multiprocess
from .utilities.VeraPDF import validate_pdf_multiprocess
from .utilities.Resources import get_project_source_path, get_project_workspace_subfolder_path, get_project_workspace_file_paths
import argparse
from datetime import datetime

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
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print(f"WORKSPACE: {args.workspace_name}")
        print(f"FOLDER: {args.workspace_folder}")
        print()

        source_path = get_project_source_path(args.project_name)
        workspace_folder_path = get_project_workspace_subfolder_path(args.project_name, args.workspace_name, args.workspace_folder)
        file_paths = get_project_workspace_file_paths(args.project_name, args.workspace_name, args.workspace_folder)

        if len(file_paths):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            total_pages = get_page_count_multiprocess(workspace_folder_path, file_paths, timestamp)
            validate_pdf_multiprocess(workspace_folder_path, file_paths, timestamp)
        else:
            print(f"No PDF files found.")
            exit()
