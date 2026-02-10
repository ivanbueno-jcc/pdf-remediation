# pylint: disable=duplicate-code
'''
Validate PDF files in a project workspace folder.
'''
import argparse
from datetime import datetime
import sys
from .utilities.pdfix import get_page_count_multiprocess
from .utilities.verapdf import validate_pdf_multiprocess
from .utilities.resources import get_project_workspace_subfolder_file_paths
from .utilities.resources import get_project_workspace_subfolder_path
from .utilities.resources import get_full_workspace_file_paths

FULL_VALIDATION_IGNORED_SUBFOLDERS = [
    "pdfix-cannot-process",
    "secured-cannot-process",
    "secured-needs-approval",
    "reports",
    "pdfix-unable-to-open",
    "unable-to-validate",
    "unable-to-process"
]

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
    parser.add_argument(
        "--full",
        action='store_true',
        help="Validate all PDFs from every workspace folder's files and processed directories."
    )
    parser.add_argument(
        "--skip-page-count",
        action='store_true',
        help="Skip page counting and only run validation."
    )
    args = parser.parse_args()

    if args.project_name:
        print(f"PROJECT: {args.project_name}")
        print(f"WORKSPACE: {args.workspace_name}")
        print(f"FULL: {args.full}")
        print(f"SKIP PAGE COUNT: {args.skip_page_count}")
        print()

        report_base_path = None
        relative_base_paths = None
        subfolder = args.directory
        workspace_folder_path = None
        file_paths = []

        if args.full:
            workspace_folder_path, scanned_paths, file_paths = get_full_workspace_file_paths(
                args.project_name,
                args.workspace_name,
                ignored_subfolders=FULL_VALIDATION_IGNORED_SUBFOLDERS
            )
            report_base_path = workspace_folder_path / "reports"
            relative_base_paths = scanned_paths
            subfolder = "full"

            print("FOLDERS SCANNED:")
            for path in scanned_paths:
                print(f"  - {path.relative_to(workspace_folder_path)}")
            print()
        else:
            print(f"FOLDER: {args.workspace_folder}")
            print(f"DIRECTORY: {args.directory}")
            print()

            workspace_folder_path = get_project_workspace_subfolder_path(
                args.project_name,
                args.workspace_name,
                args.workspace_folder,
                args.directory
            )
            file_paths = get_project_workspace_subfolder_file_paths(
                args.project_name,
                args.workspace_name,
                args.workspace_folder,
                args.directory
            )

        if len(file_paths) > 0:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if not args.skip_page_count:
                get_page_count_multiprocess(
                    workspace_folder_path,
                    file_paths,
                    timestamp,
                    subfolder,
                    report_base_path,
                    relative_base_paths
                )
            validate_pdf_multiprocess(
                workspace_folder_path,
                file_paths,
                timestamp,
                subfolder,
                report_base_path,
                relative_base_paths
            )
        else:
            print("No pending PDF files found.")
            sys.exit()
